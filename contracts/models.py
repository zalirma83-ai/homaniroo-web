from django.db import models

from common.querysets import TenantScopedManager


class Contract(models.Model):
    contract_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer_id = models.BigIntegerField(null=True, blank=True)
    counterparty_id = models.BigIntegerField(null=True, blank=True)
    parent_contract_id = models.BigIntegerField(null=True, blank=True)
    contract_direction = models.TextField()
    contract_number = models.TextField(null=True, blank=True)
    external_reference = models.TextField(null=True, blank=True)
    transaction_type = models.TextField(default="UNKNOWN")
    contract_start_jalali = models.TextField(null=True, blank=True)
    contract_start_gregorian = models.DateField(null=True, blank=True)
    contract_end_jalali = models.TextField(null=True, blank=True)
    contract_end_gregorian = models.DateField(null=True, blank=True)
    delivery_start_jalali = models.TextField(null=True, blank=True)
    delivery_start_gregorian = models.DateField(null=True, blank=True)
    delivery_end_jalali = models.TextField(null=True, blank=True)
    delivery_end_gregorian = models.DateField(null=True, blank=True)
    payment_day = models.IntegerField(null=True, blank=True)
    billing_terms = models.TextField(null=True, blank=True)
    contract_status = models.TextField(default="IMPORTED")
    source_priority = models.TextField(default="UNKNOWN")
    source_fingerprint = models.TextField(default="")
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."contracts"'
        verbose_name = "قرارداد"
        verbose_name_plural = "قراردادها"

    def __str__(self):
        return self.contract_number or f"قرارداد {self.contract_id}"


class ContractLine(models.Model):
    contract_line_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    contract = models.ForeignKey(
        Contract,
        on_delete=models.DO_NOTHING,
        db_column="contract_id",
        related_name="lines",
    )
    energy_type = models.ForeignKey(
        "energy.EnergyType",
        on_delete=models.DO_NOTHING,
        db_column="energy_type_id",
        related_name="contract_lines",
    )
    load_band = models.ForeignKey(
        "energy.LoadBand",
        on_delete=models.DO_NOTHING,
        db_column="load_band_id",
        related_name="contract_lines",
    )
    contracted_volume = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    volume_unit = models.TextField(default="MW")
    volume_unit_status = models.TextField(default="ASSUMED")
    base_rate = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    corrected_rate = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    rate_1401 = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    adjusted_rate_1401 = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    adjusted_rate_1402 = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    monthly_rate = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    rate_unit = models.TextField(default="UNKNOWN")
    amount_value = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    amount_unit = models.TextField(default="UNKNOWN")
    source_row_id = models.BigIntegerField(null=True, blank=True)
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."contract_lines"'
        ordering = ("contract_line_id",)
        verbose_name = "ردیف قرارداد"
        verbose_name_plural = "ردیف‌های قرارداد"


class ContractMonthlyQuantity(models.Model):
    monthly_quantity_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    contract_line = models.ForeignKey(
        ContractLine,
        on_delete=models.DO_NOTHING,
        db_column="contract_line_id",
        related_name="monthly_quantities",
    )
    period_jalali = models.TextField()
    quantity_type = models.TextField(default="CONTRACT_DEMAND")
    quantity_value = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    quantity_unit = models.TextField(default="MW")
    quantity_unit_status = models.TextField(default="ASSUMED")
    original_value = models.TextField(null=True, blank=True)
    source_row_id = models.BigIntegerField(null=True, blank=True)
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."contract_monthly_quantities"'
        ordering = ("period_jalali", "monthly_quantity_id")
        verbose_name = "مقدار ماهانه قرارداد"
        verbose_name_plural = "مقادیر ماهانه قرارداد"
