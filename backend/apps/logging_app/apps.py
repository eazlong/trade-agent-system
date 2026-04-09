from django.apps import AppConfig


class LoggingAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.logging_app'
    verbose_name = 'Logging'
