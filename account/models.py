import uuid
from django.db import models
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
        base = ('https://' if settings.HAS_HTTPS else 'http://') + (domain or 'localhost')
    return base.rstrip('/') + path


class User(AbstractUser, PermissionsMixin):
    username = None
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    
    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.is_verified = True 
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email
    
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