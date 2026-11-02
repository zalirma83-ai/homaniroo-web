from django.apps import AppConfig


class AuditlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auditlog"
    verbose_name = "ممیزی و امنیت"

    def ready(self):
        from . import signals  # noqa: F401
