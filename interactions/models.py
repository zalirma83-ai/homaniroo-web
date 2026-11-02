from django.db import models

from common.querysets import TenantScopedManager


class Interaction(models.Model):
    interaction_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer_id = models.BigIntegerField()
    interaction_type = models.TextField()
    interaction_at = models.DateTimeField(null=True, blank=True)
    operator_name_original = models.TextField(null=True, blank=True)
    interaction_status = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    source_system = models.TextField()
    source_record_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."interactions"'
        verbose_name = "تعامل"
        verbose_name_plural = "تعاملات"


class FollowUp(models.Model):
    follow_up_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer_id = models.BigIntegerField()
    interaction_id = models.BigIntegerField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    follow_up_status = models.TextField()
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."follow_ups"'
        verbose_name = "پیگیری"
        verbose_name_plural = "پیگیری‌ها"


class Alert(models.Model):
    alert_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer_id = models.BigIntegerField(null=True, blank=True)
    follow_up_id = models.BigIntegerField(null=True, blank=True)
    contract_id = models.BigIntegerField(null=True, blank=True)
    alert_type = models.TextField()
    alert_at = models.DateTimeField(null=True, blank=True)
    severity = models.TextField()
    alert_status = models.TextField()
    message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."alerts"'
        verbose_name = "هشدار"
        verbose_name_plural = "هشدارها"
