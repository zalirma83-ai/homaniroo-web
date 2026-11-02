from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0003_customer_status_history"),
    ]

    operations = [
        migrations.CreateModel(
            name="IndustrialUnitAlias",
            fields=[
                ("industrial_unit_alias_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("alias_name", models.TextField()),
                ("normalized_alias_name", models.TextField()),
                ("source_row_id", models.BigIntegerField()),
                ("industrial_unit", models.ForeignKey(db_column="industrial_unit_id", on_delete=django.db.models.deletion.DO_NOTHING, related_name="aliases", to="customers.industrialunit")),
            ],
            options={
                "verbose_name": "نام جایگزین واحد صنعتی",
                "verbose_name_plural": "نام‌های جایگزین واحد صنعتی",
                "db_table": '"core"."industrial_unit_aliases"',
                "ordering": ("alias_name",),
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="BillingAccount",
            fields=[
                ("billing_account_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.BigIntegerField(db_index=True)),
                ("bill_id", models.TextField()),
                ("consumer_code", models.TextField(blank=True, null=True)),
                ("regional_company", models.TextField(blank=True, null=True)),
                ("data_quality_status", models.TextField(default="REVIEW_REQUIRED")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant_customer", models.ForeignKey(db_column="tenant_customer_id", on_delete=django.db.models.deletion.DO_NOTHING, related_name="billing_accounts", to="customers.tenantcustomer")),
            ],
            options={
                "verbose_name": "حساب قبض",
                "verbose_name_plural": "حساب‌های قبض",
                "db_table": '"core"."billing_accounts"',
                "ordering": ("billing_account_id",),
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="Meter",
            fields=[
                ("meter_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.BigIntegerField(db_index=True)),
                ("meter_number", models.TextField(blank=True, null=True)),
                ("meter_type", models.TextField(blank=True, null=True)),
                ("status", models.TextField(default="PENDING_DATA")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant_customer", models.ForeignKey(db_column="tenant_customer_id", on_delete=django.db.models.deletion.DO_NOTHING, related_name="meters", to="customers.tenantcustomer")),
            ],
            options={
                "verbose_name": "کنتور",
                "verbose_name_plural": "کنتورها",
                "db_table": '"core"."meters"',
                "ordering": ("meter_id",),
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="MeterReading",
            fields=[
                ("meter_reading_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.BigIntegerField(db_index=True)),
                ("reading_at", models.DateTimeField()),
                ("consumption_value", models.DecimalField(blank=True, decimal_places=6, max_digits=30, null=True)),
                ("consumption_unit", models.TextField(blank=True, null=True)),
                ("source_name", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("meter", models.ForeignKey(db_column="meter_id", on_delete=django.db.models.deletion.DO_NOTHING, related_name="readings", to="customers.meter")),
            ],
            options={
                "verbose_name": "قرائت کنتور",
                "verbose_name_plural": "قرائت‌های کنتور",
                "db_table": '"core"."meter_readings"',
                "ordering": ("-reading_at", "-meter_reading_id"),
                "managed": False,
            },
        ),
    ]
