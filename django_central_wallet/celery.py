import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_central_wallet.settings')

app = Celery('django_central_wallet')
# All Celery settings live in Django settings with a CELERY_ prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
