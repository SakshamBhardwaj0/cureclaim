from django.apps import AppConfig


class AuditorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auditor"
    verbose_name = "CureClaim Hospital Bill Auditor"