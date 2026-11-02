from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from customers.tests import active_customer_tables, prospective_customer_tables  # shared schema fixtures
from django.utils import timezone

from contracts.models import Contract
from customers.models import IndustrialUnit, TenantCustomer
from energy.models import EnergyBalance
from interactions.models import Alert, FollowUp, Interaction

from .services import build_dashboard_context


@pytest.fixture
def dashboard_core_tables(active_customer_tables):
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS core")
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.tenant_customers (
                tenant_customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id BIGINT NOT NULL,
                industrial_unit_id BIGINT NOT NULL,
                customer_status TEXT NOT NULL DEFAULT 'LEAD',
                data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.contracts (
                contract_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id BIGINT NOT NULL,
                tenant_customer_id BIGINT,
                counterparty_id BIGINT,
                parent_contract_id BIGINT,
                contract_direction TEXT NOT NULL,
                contract_number TEXT,
                external_reference TEXT,
                transaction_type TEXT NOT NULL DEFAULT 'UNKNOWN',
                contract_start_jalali TEXT,
                contract_start_gregorian DATE,
                contract_end_jalali TEXT,
                contract_end_gregorian DATE,
                delivery_start_jalali TEXT,
                delivery_start_gregorian DATE,
                delivery_end_jalali TEXT,
                delivery_end_gregorian DATE,
                payment_day INTEGER,
                billing_terms TEXT,
                contract_status TEXT NOT NULL DEFAULT 'IMPORTED',
                source_priority TEXT NOT NULL DEFAULT 'UNKNOWN',
                source_fingerprint TEXT NOT NULL DEFAULT '',
                data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.interactions (
                interaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id BIGINT NOT NULL,
                tenant_customer_id BIGINT NOT NULL,
                interaction_type TEXT NOT NULL,
                interaction_at TIMESTAMP,
                operator_name_original TEXT,
                interaction_status TEXT,
                notes TEXT,
                source_system TEXT NOT NULL,
                source_record_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.follow_ups (
                follow_up_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id BIGINT NOT NULL,
                tenant_customer_id BIGINT NOT NULL,
                interaction_id BIGINT,
                due_at TIMESTAMP,
                follow_up_status TEXT NOT NULL,
                reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.alerts (
                alert_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id BIGINT NOT NULL,
                tenant_customer_id BIGINT,
                follow_up_id BIGINT,
                contract_id BIGINT,
                alert_type TEXT NOT NULL,
                alert_at TIMESTAMP,
                severity TEXT NOT NULL,
                alert_status TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS core.energy_balances (
                energy_balance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                validation_run_id BIGINT NOT NULL,
                tenant_id BIGINT NOT NULL,
                balance_scope TEXT NOT NULL,
                period_jalali TEXT,
                energy_type_id BIGINT NOT NULL,
                load_band_id BIGINT NOT NULL,
                purchase_volume NUMERIC NOT NULL,
                sales_volume NUMERIC NOT NULL,
                balance_value NUMERIC NOT NULL,
                balance_status TEXT NOT NULL,
                volume_unit TEXT NOT NULL,
                data_completeness TEXT NOT NULL,
                calculated_at TIMESTAMPTZ NOT NULL
            )"""
        )
    return True


@pytest.mark.django_db
def test_dashboard_metrics_are_tenant_scoped(dashboard_core_tables):
    today = timezone.localdate()
    first = SimpleNamespace(tenant_id=1)
    second = SimpleNamespace(tenant_id=2)

    # Let PostgreSQL generate identity values; never assume their sequence.
    units = [
        IndustrialUnit.objects.create(
            official_name=f"واحد {label}", normalized_name=f"unit-{label}",
            data_quality_status="CLEAN",
        )
        for label in ("first-active", "first-lead", "second-active")
    ]
    first_active = TenantCustomer.objects.create(
        tenant_id=1, industrial_unit=units[0], customer_status="ACTIVE",
        data_quality_status="CLEAN",
    )
    first_lead = TenantCustomer.objects.create(
        tenant_id=1, industrial_unit=units[1], customer_status="LEAD",
        data_quality_status="REVIEW_REQUIRED",
    )
    second_active = TenantCustomer.objects.create(
        tenant_id=2, industrial_unit=units[2], customer_status="ACTIVE",
        data_quality_status="CLEAN",
    )

    Contract.objects.create(
        tenant_id=1, tenant_customer_id=first_active.tenant_customer_id, contract_direction="SALE",
        contract_start_gregorian=today - timedelta(days=10),
        contract_end_gregorian=today + timedelta(days=5),
        source_fingerprint="first-sale",
    )
    Contract.objects.create(
        tenant_id=1, tenant_customer_id=first_lead.tenant_customer_id, contract_direction="PURCHASE",
        contract_start_gregorian=today - timedelta(days=20),
        contract_end_gregorian=today + timedelta(days=40),
        source_fingerprint="first-purchase",
    )
    Contract.objects.create(
        tenant_id=2, tenant_customer_id=second_active.tenant_customer_id, contract_direction="SALE",
        contract_start_gregorian=today - timedelta(days=2),
        contract_end_gregorian=today + timedelta(days=2),
        source_fingerprint="second-sale",
    )

    FollowUp.objects.create(
        tenant_id=1, tenant_customer_id=first_active.tenant_customer_id, due_at=timezone.now(),
        follow_up_status="OPEN",
    )
    FollowUp.objects.create(
        tenant_id=1, tenant_customer_id=first_lead.tenant_customer_id,
        due_at=timezone.now() - timedelta(days=2), follow_up_status="OPEN",
    )
    FollowUp.objects.create(
        tenant_id=2, tenant_customer_id=second_active.tenant_customer_id, due_at=timezone.now(),
        follow_up_status="OPEN",
    )

    Interaction.objects.create(
        tenant_id=1, tenant_customer_id=first_active.tenant_customer_id, interaction_type="CALL",
        interaction_at=timezone.now(), interaction_status="کسی جواب نداد",
        source_system="TEST",
    )
    Interaction.objects.create(
        tenant_id=1, tenant_customer_id=first_lead.tenant_customer_id, interaction_type="CALL",
        interaction_at=timezone.now(), interaction_status="پاسخ داده شد",
        source_system="TEST",
    )
    Interaction.objects.create(
        tenant_id=2, tenant_customer_id=second_active.tenant_customer_id, interaction_type="CALL",
        interaction_at=timezone.now(), interaction_status="کسی جواب نداد",
        source_system="TEST",
    )

    Alert.objects.create(
        tenant_id=1, tenant_customer_id=first_active.tenant_customer_id, alert_type="FOLLOW_UP",
        severity="WARNING", alert_status="OPEN",
    )
    Alert.objects.create(
        tenant_id=2, tenant_customer_id=second_active.tenant_customer_id, alert_type="FOLLOW_UP",
        severity="WARNING", alert_status="OPEN",
    )

    EnergyBalance.objects.create(
        validation_run_id=1, tenant_id=1, balance_scope="CONTRACT_TOTAL",
        energy_type_id=1, load_band_id=1, purchase_volume=Decimal("100"),
        sales_volume=Decimal("140"), balance_value=Decimal("-40"),
        balance_status="DEFICIT", volume_unit="kWh", data_completeness="COMPLETE",
        calculated_at=timezone.now(),
    )
    EnergyBalance.objects.create(
        validation_run_id=1, tenant_id=2, balance_scope="CONTRACT_TOTAL",
        energy_type_id=1, load_band_id=1, purchase_volume=Decimal("500"),
        sales_volume=Decimal("100"), balance_value=Decimal("400"),
        balance_status="SURPLUS", volume_unit="kWh", data_completeness="COMPLETE",
        calculated_at=timezone.now(),
    )

    context = build_dashboard_context(first)

    assert context["customers"]["total"] == 2
    assert context["customers"]["active"] == 1
    assert context["contracts"]["total"] == 2
    assert context["contracts"]["active_display"] == 2
    assert context["contracts"]["expiring_display"] == 1
    assert context["follow_ups"]["today"] == 1
    assert context["follow_ups"]["overdue"] == 1
    assert context["interactions"]["missed"] == 1
    assert context["alerts"]["open"] == 1
    assert context["energy"]["purchase"] == Decimal("100")
    assert context["energy"]["balance_label"] == "کسری"
    assert build_dashboard_context(second)["customers"]["total"] == 1


@pytest.mark.django_db
def test_contract_date_metrics_are_unknown_when_dates_are_missing(
    dashboard_core_tables,
):
    tenant = SimpleNamespace(tenant_id=10)
    Contract.objects.create(
        tenant_id=10, contract_direction="SALE", source_fingerprint="missing-date"
    )

    context = build_dashboard_context(tenant)

    assert context["contracts"]["dates_available"] is False
    assert context["contracts"]["active_display"] is None
    assert context["contracts"]["expiring_display"] is None
    assert context["quality"]["contracts_missing_dates"] == 1


@pytest.mark.django_db
def test_dashboard_without_tenant_returns_no_cross_tenant_data(
    dashboard_core_tables,
):
    TenantCustomer.objects.create(
        tenant_id=1, industrial_unit_id=101, customer_status="ACTIVE"
    )

    context = build_dashboard_context(None)

    assert context["dashboard_ready"] is False
    assert context["customers"] == {}
