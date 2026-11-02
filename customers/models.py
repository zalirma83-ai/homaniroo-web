from django.db import models

from common.querysets import TenantScopedManager


class IndustrialUnit(models.Model):
    industrial_unit_id = models.BigAutoField(primary_key=True)
    official_name = models.TextField()
    normalized_name = models.TextField(db_index=True)
    national_id = models.TextField(null=True, blank=True)
    economic_code = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    ownership_type = models.TextField(default="UNKNOWN")
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = '"core"."industrial_units"'
        ordering = ("official_name",)
        verbose_name = "واحد صنعتی"
        verbose_name_plural = "واحدهای صنعتی"

    def __str__(self):
        return self.official_name


class TenantCustomer(models.Model):
    tenant_customer_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    industrial_unit = models.ForeignKey(
        IndustrialUnit,
        on_delete=models.DO_NOTHING,
        db_column="industrial_unit_id",
        related_name="tenant_customers",
    )
    customer_status = models.TextField(default="LEAD")
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."tenant_customers"'
        verbose_name = "مشتری شرکت"
        verbose_name_plural = "مشتریان شرکت"

    def __str__(self):
        return self.industrial_unit.official_name


class CustomerContact(models.Model):
    contact_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer = models.ForeignKey(
        TenantCustomer,
        on_delete=models.DO_NOTHING,
        db_column="tenant_customer_id",
        related_name="contacts",
    )
    contact_person = models.TextField(null=True, blank=True)
    mobile_number = models.TextField(null=True, blank=True)
    phone_number = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."customer_contacts"'
        ordering = ("-is_primary", "contact_id")
        verbose_name = "اطلاعات تماس مشتری"
        verbose_name_plural = "اطلاعات تماس مشتریان"

    def __str__(self):
        return self.contact_person or self.mobile_number or self.phone_number or "اطلاعات تماس"


class CustomerStatusHistory(models.Model):
    history_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer_id = models.BigIntegerField(db_index=True)
    previous_status = models.TextField()
    new_status = models.TextField()
    reason_code = models.TextField()
    reason_note = models.TextField(null=True, blank=True)
    changed_by_user_id = models.BigIntegerField(null=True, blank=True)
    changed_by_name = models.TextField()
    changed_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."customer_status_history"'
        ordering = ("-changed_at", "-history_id")
        verbose_name = "سابقه وضعیت مشتری"
        verbose_name_plural = "سوابق وضعیت مشتریان"

    def __str__(self):
        return f"{self.previous_status} → {self.new_status}"


class IndustrialUnitAlias(models.Model):
    industrial_unit_alias_id = models.BigAutoField(primary_key=True)
    industrial_unit = models.ForeignKey(
        IndustrialUnit,
        on_delete=models.DO_NOTHING,
        db_column="industrial_unit_id",
        related_name="aliases",
    )
    alias_name = models.TextField()
    normalized_alias_name = models.TextField()
    source_row_id = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = '"core"."industrial_unit_aliases"'
        ordering = ("alias_name",)
        verbose_name = "نام جایگزین واحد صنعتی"
        verbose_name_plural = "نام‌های جایگزین واحد صنعتی"


class BillingAccount(models.Model):
    billing_account_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer = models.ForeignKey(
        TenantCustomer,
        on_delete=models.DO_NOTHING,
        db_column="tenant_customer_id",
        related_name="billing_accounts",
    )
    bill_id = models.TextField()
    consumer_code = models.TextField(null=True, blank=True)
    regional_company = models.TextField(null=True, blank=True)
    data_quality_status = models.TextField(default="REVIEW_REQUIRED")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."billing_accounts"'
        ordering = ("billing_account_id",)
        verbose_name = "حساب قبض"
        verbose_name_plural = "حساب‌های قبض"


class Meter(models.Model):
    meter_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    tenant_customer = models.ForeignKey(
        TenantCustomer,
        on_delete=models.DO_NOTHING,
        db_column="tenant_customer_id",
        related_name="meters",
    )
    meter_number = models.TextField(null=True, blank=True)
    meter_type = models.TextField(null=True, blank=True)
    status = models.TextField(default="PENDING_DATA")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."meters"'
        ordering = ("meter_id",)
        verbose_name = "کنتور"
        verbose_name_plural = "کنتورها"


class MeterReading(models.Model):
    meter_reading_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField(db_index=True)
    meter = models.ForeignKey(
        Meter,
        on_delete=models.DO_NOTHING,
        db_column="meter_id",
        related_name="readings",
    )
    reading_at = models.DateTimeField()
    consumption_value = models.DecimalField(
        max_digits=30, decimal_places=6, null=True, blank=True
    )
    consumption_unit = models.TextField(null=True, blank=True)
    source_name = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."meter_readings"'
        ordering = ("-reading_at", "-meter_reading_id")
        verbose_name = "قرائت کنتور"
        verbose_name_plural = "قرائت‌های کنتور"


class EntityBusinessProfile(models.Model):
    """Read existing approved classifications; no automatic customer lifecycle changes."""
    profile_id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    industrial_unit = models.ForeignKey(
        IndustrialUnit, on_delete=models.DO_NOTHING,
        db_column="industrial_unit_id", related_name="business_profiles",
    )
    display_name = models.TextField()
    relationship_type = models.TextField()
    purchase_cycle = models.TextField()
    classification_status = models.TextField()
    review_reason = models.TextField(null=True, blank=True)
    missing_in_initial_database = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)
    sources = models.JSONField(default=list)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    provided_national_id = models.TextField(null=True, blank=True)

    objects = TenantScopedManager()

    class Meta:
        managed = False
        db_table = '"core"."entity_business_profiles"'
        ordering = ("display_name", "profile_id")
        verbose_name = "دسته‌بندی تجاری"
        verbose_name_plural = "دسته‌بندی‌های تجاری"


OWNERSHIP_CHOICES = (("UNKNOWN", "نامشخص"), ("PUBLIC", "دولتی"), ("PRIVATE", "خصوصی"))


class EntityOwnership(models.Model):
    """Operator-owned classification for a unit within one tenant, shared by every screen."""
    tenant_id = models.BigIntegerField()
    industrial_unit_id = models.BigIntegerField()
    ownership_type = models.CharField(max_length=7, choices=OWNERSHIP_CHOICES, default="UNKNOWN")
    changed_by_user_id = models.BigIntegerField(null=True, blank=True)
    changed_by_name = models.CharField(max_length=300, blank=True)
    changed_at = models.DateTimeField(auto_now=True)
    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant_id", "industrial_unit_id"), name="ownership_tenant_unit_unique"),
            models.CheckConstraint(condition=models.Q(ownership_type__in=["UNKNOWN", "PUBLIC", "PRIVATE"]), name="ownership_valid_type"),
        ]


class EntityOwnershipChange(models.Model):
    tenant_id = models.BigIntegerField()
    industrial_unit_id = models.BigIntegerField()
    previous_type = models.CharField(max_length=7, choices=OWNERSHIP_CHOICES)
    new_type = models.CharField(max_length=7, choices=OWNERSHIP_CHOICES)
    changed_by_user_id = models.BigIntegerField(null=True, blank=True)
    changed_by_name = models.CharField(max_length=300)
    changed_at = models.DateTimeField(auto_now_add=True)
    objects = TenantScopedManager()

    class Meta:
        ordering = ("-changed_at", "-id")


class PartnerNote(models.Model):
    """One operator-owned note per tenant/business profile; never imported notes."""
    tenant_id = models.PositiveBigIntegerField()
    profile_id = models.PositiveBigIntegerField()
    body = models.TextField(blank=True, default="")
    version = models.PositiveBigIntegerField(default=1)
    updated_by_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    updated_by_name = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant_id", "profile_id"), name="partner_note_tenant_profile_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="partner_note_version_positive"),
            models.CheckConstraint(condition=models.Q(tenant_id__gte=1, profile_id__gte=1), name="partner_note_scope_positive"),
        ]


class CustomerSourceNote(models.Model):
    """Original document note, distinct from a dated contact or purchase."""
    tenant_id = models.PositiveBigIntegerField(db_index=True)
    # Legacy core tables are unmanaged; imports validate company/customer/bill together.
    tenant_customer_id = models.PositiveBigIntegerField(db_index=True)
    billing_account_id = models.PositiveBigIntegerField()
    body = models.TextField()
    body_sha256 = models.CharField(max_length=64)
    source_filename = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    source_sheet = models.CharField(max_length=100)
    source_row = models.PositiveIntegerField()
    source_column = models.CharField(max_length=3)
    imported_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        ordering = ("source_row", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant_id", "source_sha256", "source_sheet", "source_row", "source_column"),
                name="source_note_origin_uniq",
            ),
            models.CheckConstraint(condition=models.Q(tenant_id__gte=1, tenant_customer_id__gte=1, billing_account_id__gte=1, source_row__gte=2), name="source_note_scope_positive"),
            models.CheckConstraint(condition=~models.Q(body=""), name="source_note_body_nonempty"),
        ]


class CustomerExcelSourceRow(models.Model):
    """Complete original Excel row, with explicitly confirmed contractual demand."""
    tenant_id = models.PositiveBigIntegerField(db_index=True)
    tenant_customer_id = models.PositiveBigIntegerField(db_index=True)
    billing_account_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    source_filename = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    source_sheet = models.CharField(max_length=100)
    source_row = models.PositiveIntegerField()
    raw_cells = models.JSONField()
    source_headers = models.JSONField(default=dict)
    cell_types = models.JSONField(default=dict)
    demand_mw = models.DecimalField(max_digits=30, decimal_places=6, null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        ordering = ("source_row", "id")
        constraints = [
            models.UniqueConstraint(fields=("tenant_id", "source_sha256", "source_sheet", "source_row"), name="excel_row_origin_uniq"),
            models.CheckConstraint(condition=models.Q(tenant_id__gte=1, tenant_customer_id__gte=1, source_row__gte=2), name="excel_row_scope_positive"),
            models.CheckConstraint(condition=models.Q(billing_account_id__isnull=True) | models.Q(billing_account_id__gte=1), name="excel_row_bill_positive"),
            models.CheckConstraint(condition=models.Q(demand_mw__isnull=True) | models.Q(demand_mw__gte=0), name="excel_row_demand_nonnegative"),
        ]
