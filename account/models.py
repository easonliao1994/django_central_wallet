import uuid
from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.models import AbstractUser, PermissionsMixin
from .manager import UserManager
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from rest_framework.authtoken.models import Token

def site_url(path: str, domain: str = None) -> str:
    """Absolute URL for links in emails. Uses settings.SITE_URL; the request host is only a
    development fallback (it comes from the Host header, so it must not be trusted in production)."""
    base = getattr(settings, 'SITE_URL', '')
    if not base:
        if not settings.DEBUG:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured('Set SITE_URL: emailed links must not be built from the request Host header')
        base = ('https://' if settings.HAS_HTTPS else 'http://') + (domain or 'localhost')
    return base.rstrip('/') + path


class User(AbstractUser, PermissionsMixin):
    username = None
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Two-factor authentication (TOTP). The secret is encrypted with WALLET_ENCRYPTION_KEYS; it is set
    # when setup starts and 2FA is on once totp_enabled_at is set.
    totp_secret = models.TextField(blank=True, default='', editable=False)
    totp_enabled_at = models.DateTimeField(null=True, blank=True, editable=False)
    totp_last_step = models.BigIntegerField(null=True, blank=True, editable=False,
                                            help_text='Time step of the last accepted code (codes are single use).')
    # Withdrawal address whitelist: turning it off takes effect after WITHDRAWAL_ADDRESS_COOLDOWN_HOURS.
    withdrawal_whitelist_enabled = models.BooleanField(default=False)
    whitelist_disable_requested_at = models.DateTimeField(null=True, blank=True)
    withdrawals_locked_until = models.DateTimeField(
        null=True, blank=True, help_text='Withdrawals and transfers are blocked until then (set after a password reset or 2FA disabled).')

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower('email'), name='unique_user_email_case_insensitive'),
        ]

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    
    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.is_verified = True 
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email

    @property
    def two_factor_enabled(self) -> bool:
        return self.totp_enabled_at is not None and bool(self.totp_secret)
    
    @property
    def get_user_fullname(self):
        return f"{self.first_name} {self.last_name}"
    
    def send_verification_mail(self, domain=None):
        subject = _('DjangoCentralWallet - Email Verification')
        url = site_url(self.activation_path(), domain)
        html_message = render_to_string('verification_mail.html', {'email': self.email, 'verification_url': url})
        self.email_user(subject, strip_tags(html_message), settings.DEFAULT_FROM_EMAIL, html_message=html_message)

    def send_password_reset_mail(self, domain=None):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        uid = urlsafe_base64_encode(force_bytes(self.pk))
        token = default_token_generator.make_token(self)
        template = getattr(settings, 'PASSWORD_RESET_URL', '')
        reset_url = template.format(uid=uid, token=token) if template else None
        html_message = render_to_string('password_reset_mail.html', {
            'email': self.email, 'reset_url': reset_url, 'uid': uid, 'token': token,
            'api_url': site_url('/account/password/reset/', domain),
        })
        self.email_user(_('DjangoCentralWallet - Reset your password'), strip_tags(html_message),
                        settings.DEFAULT_FROM_EMAIL, html_message=html_message)

    def send_security_notice(self, subject, lines):
        html_message = render_to_string('security_notice_mail.html', {'email': self.email, 'lines': lines})
        self.email_user(f'DjangoCentralWallet - {subject}', strip_tags(html_message), settings.DEFAULT_FROM_EMAIL,
                        html_message=html_message)

    def activation_path(self):
        from django.urls import reverse
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        from account.tokens import EmailVerifyTokenGenerator
        return reverse('activate', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(self.pk)),
            'token': EmailVerifyTokenGenerator().make_token(self),
        })
    

    def activate(self, verify_method: str):
        self.is_active = True

        if verify_method == 'email':
            self.is_verified = True
        self.save()

        if self.is_verified:
            from blockchain.wallet import get_or_create_deposit_address
            get_or_create_deposit_address(self)


class WithdrawalAddress(models.Model):
    """A saved withdrawal address. Usable from ``active_from`` (WITHDRAWAL_ADDRESS_COOLDOWN_HOURS after it
    was added); when the user's whitelist mode is on, withdrawals can only go to these addresses."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='withdrawal_addresses')
    label = models.CharField(max_length=64, blank=True, default='')
    address = models.CharField(max_length=64, help_text='EIP-55 checksummed address (valid on every EVM chain).')
    created_at = models.DateTimeField(auto_now_add=True)
    active_from = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'address'], name='unique_withdrawal_address_per_user'),
        ]

    def __str__(self):
        return f'{self.user} {self.label or self.address}'
