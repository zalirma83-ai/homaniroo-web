from django.db import migrations, models


CREATE_SQL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.customer_status_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    tenant_customer_id BIGINT NOT NULL,
    previous_status TEXT NOT NULL CHECK (previous_status IN ('LEAD', 'ACTIVE', 'DORMANT')),
    new_status TEXT NOT NULL CHECK (new_status IN ('LEAD', 'ACTIVE', 'DORMANT')),
    reason_code TEXT NOT NULL,
    reason_note TEXT,
    changed_by_user_id BIGINT,
    changed_by_name TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_customer_status_history_tenant_customer
    ON core.customer_status_history (tenant_id, tenant_customer_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS ix_customer_status_history_new_status
    ON core.customer_status_history (tenant_id, new_status, changed_at DESC);
"""

DROP_SQL = "DROP TABLE IF EXISTS core.customer_status_history;"


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0002_customercontact_industrialunit"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, reverse_sql=DROP_SQL),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="CustomerStatusHistory",
                    fields=[
                        ("history_id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField(db_index=True)),
                        ("tenant_customer_id", models.BigIntegerField(db_index=True)),
                        ("previous_status", models.TextField()),
                        ("new_status", models.TextField()),
                        ("reason_code", models.TextField()),
                        ("reason_note", models.TextField(blank=True, null=True)),
                        ("changed_by_user_id", models.BigIntegerField(blank=True, null=True)),
                        ("changed_by_name", models.TextField()),
                        ("changed_at", models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "verbose_name": "سابقه وضعیت مشتری",
                        "verbose_name_plural": "سوابق وضعیت مشتریان",
                        "db_table": '"core"."customer_status_history"',
                        "ordering": ("-changed_at", "-history_id"),
                        "managed": False,
                    },
                ),
            ],
        ),
    ]
