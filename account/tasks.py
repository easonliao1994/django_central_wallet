"""Emails are sent by the Celery worker: requests do not wait for SMTP, and the response time
does not reveal whether an account exists."""
from celery import shared_task
from django.contrib.auth import get_user_model


@shared_task(name='account.send_verification_email')
def send_verification_email(user_id: int, domain: str = None):
    user = get_user_model().objects.filter(pk=user_id, is_verified=False).first()
    if user is not None:
        user.send_verification_mail(domain)


@shared_task(name='account.send_password_reset_email')
def send_password_reset_email(user_id: int, domain: str = None):
    user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if user is not None and user.has_usable_password():
        user.send_password_reset_mail(domain)
