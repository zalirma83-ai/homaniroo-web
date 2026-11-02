from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("auditlog", "0001_initial")]
    operations = [
        migrations.RunSQL(
            sql="CREATE SCHEMA IF NOT EXISTS core",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("audit_log_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("auth_user_id", models.BigIntegerField(blank=True, db_index=True, null=True)),
                ("core_user_id", models.BigIntegerField(blank=True, null=True)),
                ("username", models.TextField(blank=True, default="")),
                ("action", models.CharField(choices=[("LOGIN_SUCCESS", "ورود موفق"), ("LOGIN_FAILED", "ورود ناموفق"), ("LOGOUT", "خروج از سامانه"), ("CREATE_OR_UPDATE", "ایجاد یا ویرایش"), ("DELETE", "حذف"), ("STATUS_CHANGE", "تغییر وضعیت"), ("SYSTEM", "عملیات سامانه")], db_index=True, max_length=64)),
                ("entity_type", models.CharField(blank=True, default="", max_length=100)),
                ("entity_id", models.TextField(blank=True, default="")),
                ("description", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("request_path", models.TextField(blank=True, default="")),
                ("request_method", models.CharField(blank=True, default="", max_length=10)),
                ("success", models.BooleanField(db_index=True, default=True)),
                ("severity", models.CharField(choices=[("NORMAL", "عادی"), ("IMPORTANT", "مهم"), ("CRITICAL", "بحرانی")], db_index=True, default="NORMAL", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "verbose_name": "رویداد ممیزی",
                "verbose_name_plural": "رویدادهای ممیزی",
                "db_table": '"core"."audit_logs"',
                "ordering": ("-created_at", "-audit_log_id"),
                "indexes": [
                    models.Index(fields=["tenant_id", "created_at"], name="audit_tenant_time_idx"),
                    models.Index(fields=["auth_user_id", "created_at"], name="audit_user_time_idx"),
                ],
            },
        ),
    ]
