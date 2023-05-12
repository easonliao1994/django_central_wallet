import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser, PermissionsMixin
from .manager import UserManager
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from rest_framework.authtoken.models import Token

# Create your models here.
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
    
    def send_verification_mail(self, domain):
        subject = _(f'DjangoCentralWallet - Email Verification')
        url = ('https://' if settings.HAS_HTTPS else 'http://') + domain + self.activation_path()
        html_message = render_to_string('verification_mail.html', {'email': self.email, 'verification_url': url})
        plain_message = strip_tags(html_message)
        from_email = settings.DEFAULT_FROM_EMAIL
        self.email_user(subject, plain_message, from_email, html_message=html_message)

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