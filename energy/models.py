from django.db import models

from common.querysets import TenantScopedManager


class EnergyType(models.Model):
    energy_type_id = models.BigAutoField(primary_key=True)
    energy_type_code = models.TextField(unique=True)
    energy_type_name_fa = models.TextField()
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = '"core"."energy_types"'
        ordering = ("energy_type_id",)
        verbose_name = "نوع برق"
        verbose_name_plural = "انواع برق"

    def __str__(self):
        return self.energy_type_name_fa


class LoadBand(models.Model):
    load_band_id = models.BigAutoField(primary_key=True)
    load_band_code = models.TextField(unique=True)
    load_band_name_fa = models.TextField()
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = '"core"."load_bands"'
        ordering = ("load_band_id",)
        verbose_name = "بازه بار"
        verbose_name_plural = "بازه‌های بار"

    def __str__(self):
        return self.load_band_name_fa


class EnergyBalance(models.Model):
    energy_balance_id = models.BigAutoField(primary_key=True)
    validation_run_id = models.BigIntegerField()
    tenant_id = models.BigIntegerField(db_index=True)
    balance_scope = models.TextField()
    period_jalali = models.TextField(null=True, blank=True)
    energy_type_id = models.BigIntegerField()
    load_band_id = models.BigIntegerField()
    purchase_volume = models.DecimalField(max_digits=30, decimal_places=6)
    sales_volume = models.DecimalField(max_digits=30, decimal_places=6)
    balance_value = models.DecimalField(max_digits=30, decimal_places=6)
    balance_status = models.TextField()
    volume_unit = models.TextField()
    data_completeness = models.TextField()
    calculated_at = models.DateTimeField()

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."energy_balances"'
        verbose_name = "تراز انرژی"
        verbose_name_plural = "ترازهای انرژی"
