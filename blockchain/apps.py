from django.apps import AppConfig


class BlockchainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'blockchain'

    def ready(self):
        from . import monitoring, signals  # noqa: F401  (monitoring: Celery task_failure alerts)
