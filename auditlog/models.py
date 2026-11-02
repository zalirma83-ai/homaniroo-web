from django.db import models


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ("LOGIN_SUCCESS", "ورود موفق"),
        ("LOGIN_FAILED", "ورود ناموفق"),
        ("LOGOUT", "خروج از سامانه"),
        ("CREATE_OR_UPDATE", "ایجاد یا ویرایش"),
        ("DELETE", "حذف"),
        ("STATUS_CHANGE", "تغییر وضعیت"),
        ("SYSTEM", "عملیات سامانه"),
    )
    SEVERITY_CHOICES = (("NORMAL", "عادی"), ("IMPORTANT", "مهم"), ("CRITICAL", "بحرانی"))

    audit_log_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    auth_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    core_user_id = models.BigIntegerField(null=True, blank=True)
    username = models.TextField(blank=True, default="")
    action = models.CharField(max_length=64, db_index=True, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=100, blank=True, default="")
    entity_id = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_path = models.TextField(blank=True, default="")
    request_method = models.CharField(max_length=10, blank=True, default="")
    success = models.BooleanField(default=True, db_index=True)
    severity = models.CharField(max_length=16, default="NORMAL", db_index=True, choices=SEVERITY_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = '"core"."audit_logs"'
        ordering = ("-created_at", "-audit_log_id")
        indexes = [
            models.Index(fields=("tenant_id", "created_at"), name="audit_tenant_time_idx"),
            models.Index(fields=("auth_user_id", "created_at"), name="audit_user_time_idx"),
        ]
        verbose_name = "رویداد ممیزی"
        verbose_name_plural = "رویدادهای ممیزی"

    def __str__(self):
        return f"{self.action} - {self.username or 'سامانه'}"
