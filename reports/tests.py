from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from customers.tests import active_customer_tables, prospective_customer_tables  # shared schema fixtures
from django.utils import timezone

from contracts.models import Contract, ContractLine
from customers.models import CustomerContact, IndustrialUnit, TenantCustomer
from energy.models import EnergyBalance, EnergyType, LoadBand
from interactions.models import Interaction
from .services import build_management_report, export_rows, parse_report_dates


@pytest.fixture
def report_tables(active_customer_tables):
    statements = [
        "CREATE SCHEMA IF NOT EXISTS core",
        """CREATE TABLE IF NOT EXISTS core.industrial_units (industrial_unit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, official_name TEXT NOT NULL, normalized_name TEXT NOT NULL, national_id TEXT, economic_code TEXT, postal_code TEXT, address TEXT, ownership_type TEXT NOT NULL DEFAULT 'UNKNOWN', data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.tenant_customers (tenant_customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, industrial_unit_id BIGINT NOT NULL, customer_status TEXT NOT NULL DEFAULT 'LEAD', data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.customer_contacts (contact_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, tenant_customer_id BIGINT NOT NULL, contact_person TEXT, mobile_number TEXT, phone_number TEXT, email TEXT, website TEXT, is_primary BOOLEAN NOT NULL DEFAULT FALSE, data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.energy_types (energy_type_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, energy_type_code TEXT NOT NULL UNIQUE, energy_type_name_fa TEXT NOT NULL, description TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE)""",
        """CREATE TABLE IF NOT EXISTS core.load_bands (load_band_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, load_band_code TEXT NOT NULL UNIQUE, load_band_name_fa TEXT NOT NULL, description TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE)""",
        """CREATE TABLE IF NOT EXISTS core.contracts (contract_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, tenant_customer_id BIGINT, counterparty_id BIGINT, parent_contract_id BIGINT, contract_direction TEXT NOT NULL, contract_number TEXT, external_reference TEXT, transaction_type TEXT NOT NULL DEFAULT 'UNKNOWN', contract_start_jalali TEXT, contract_start_gregorian DATE, contract_end_jalali TEXT, contract_end_gregorian DATE, delivery_start_jalali TEXT, delivery_start_gregorian DATE, delivery_end_jalali TEXT, delivery_end_gregorian DATE, payment_day INTEGER, billing_terms TEXT, contract_status TEXT NOT NULL DEFAULT 'IMPORTED', source_priority TEXT NOT NULL DEFAULT 'PRIMARY', source_fingerprint TEXT NOT NULL, data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.contract_lines (contract_line_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, contract_id BIGINT NOT NULL, energy_type_id BIGINT NOT NULL, load_band_id BIGINT NOT NULL, contracted_volume NUMERIC(30,6), volume_unit TEXT NOT NULL DEFAULT 'MW', volume_unit_status TEXT NOT NULL DEFAULT 'ASSUMED', base_rate NUMERIC(30,6), corrected_rate NUMERIC(30,6), rate_1401 NUMERIC(30,6), adjusted_rate_1401 NUMERIC(30,6), adjusted_rate_1402 NUMERIC(30,6), monthly_rate NUMERIC(30,6), rate_unit TEXT NOT NULL DEFAULT 'UNKNOWN', amount_value NUMERIC(30,6), amount_unit TEXT NOT NULL DEFAULT 'UNKNOWN', source_row_id BIGINT, data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.interactions (interaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, tenant_customer_id BIGINT NOT NULL, interaction_type TEXT NOT NULL, interaction_at TIMESTAMPTZ, operator_name_original TEXT, interaction_status TEXT, notes TEXT, source_system TEXT NOT NULL, source_record_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.follow_ups (follow_up_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, tenant_customer_id BIGINT NOT NULL, interaction_id BIGINT, due_at TIMESTAMPTZ, follow_up_status TEXT NOT NULL, reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.alerts (alert_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id BIGINT NOT NULL, tenant_customer_id BIGINT, follow_up_id BIGINT, contract_id BIGINT, alert_type TEXT NOT NULL, alert_at TIMESTAMPTZ, severity TEXT NOT NULL, alert_status TEXT NOT NULL, message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS core.energy_balances (energy_balance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, validation_run_id BIGINT NOT NULL, tenant_id BIGINT NOT NULL, balance_scope TEXT NOT NULL, period_jalali TEXT, energy_type_id BIGINT NOT NULL, load_band_id BIGINT NOT NULL, purchase_volume NUMERIC NOT NULL DEFAULT 0, sales_volume NUMERIC NOT NULL DEFAULT 0, balance_value NUMERIC NOT NULL DEFAULT 0, balance_status TEXT NOT NULL, volume_unit TEXT NOT NULL DEFAULT 'MW', data_completeness TEXT NOT NULL, calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def make_customer(tenant_id, name, status="ACTIVE"):
    unit = IndustrialUnit.objects.create(official_name=name, normalized_name=name, ownership_type="UNKNOWN", data_quality_status="CLEAN")
    return TenantCustomer.objects.create(tenant_id=tenant_id, industrial_unit=unit, customer_status=status, data_quality_status="CLEAN")


def make_catalog():
    energy, _ = EnergyType.objects.get_or_create(energy_type_code="REPORT_TEST", defaults={"energy_type_name_fa": "برق آزمون", "is_active": True})
    load, _ = LoadBand.objects.get_or_create(load_band_code="REPORT_BASE", defaults={"load_band_name_fa": "بار آزمون", "is_active": True})
    return energy, load


def make_contract(customer, fingerprint, volume="10"):
    energy, load = make_catalog()
    contract = Contract.objects.create(tenant_id=customer.tenant_id, tenant_customer_id=customer.tenant_customer_id, contract_direction="SALE", contract_number=fingerprint, transaction_type="BILATERAL", contract_status="IMPORTED", source_priority="PRIMARY", source_fingerprint=fingerprint, data_quality_status="CLEAN")
    ContractLine.objects.create(tenant_id=customer.tenant_id, contract=contract, energy_type=energy, load_band=load, contracted_volume=Decimal(volume), volume_unit="MW", volume_unit_status="CONFIRMED", base_rate=Decimal("2"), amount_value=Decimal("20"), rate_unit="RIAL", amount_unit="RIAL", data_quality_status="CLEAN")
    return contract


def make_balance(tenant_id, run_id, purchase="100", sales="140"):
    energy, load = make_catalog()
    purchase, sales = Decimal(purchase), Decimal(sales)
    return EnergyBalance.objects.create(validation_run_id=run_id, tenant_id=tenant_id, balance_scope="CONTRACT_TOTAL", energy_type_id=energy.energy_type_id, load_band_id=load.load_band_id, purchase_volume=purchase, sales_volume=sales, balance_value=purchase-sales, balance_status="DEFICIT", volume_unit="MW", data_completeness="COMPLETE", calculated_at=timezone.now())


@pytest.mark.django_db
def test_management_report_is_tenant_scoped(report_tables):
    first = make_customer(1, "شرکت اول")
    second = make_customer(2, "شرکت دوم")
    make_contract(first, "first")
    make_contract(second, "second")
    context = build_management_report(SimpleNamespace(tenant_id=1))
    assert context["customers"]["total"] == 1
    assert context["contracts"]["total"] == 1


@pytest.mark.django_db
def test_customer_contact_coverage_is_reported(report_tables):
    customer = make_customer(1, "دارای تماس")
    make_customer(1, "بدون تماس")
    CustomerContact.objects.create(tenant_id=1, tenant_customer=customer, contact_person="مسئول انرژی", mobile_number="09120000000", is_primary=True, data_quality_status="CLEAN")
    context = build_management_report(SimpleNamespace(tenant_id=1))
    assert context["customers"]["with_contact"] == 1
    assert context["customers"]["without_contact"] == 1


def test_report_dates_are_safe_and_reversed_when_needed():
    start_date, end_date, start, end = parse_report_dates("2026-10-10", "2026-10-01")
    assert start_date.isoformat() == "2026-10-01"
    assert end_date.isoformat() == "2026-10-10"
    assert end > start
    assert parse_report_dates("not-a-date", "")[0] is None


@pytest.mark.django_db
@pytest.mark.parametrize("instant", [
    "2026-10-17T20:29:59+00:00",  # Before Tehran midnight.
    "2026-10-17T20:30:00+00:00",  # Tehran midnight, still previous UTC date.
    "2026-10-17T23:59:59+00:00",
    "2026-10-18T00:00:00+00:00",
])
def test_interaction_report_respects_date_range(report_tables, instant):
    with timezone.override("Asia/Tehran"):
        customer = make_customer(1, "مشتری تماس")
        now = datetime.fromisoformat(instant)
        today = timezone.localdate(now)
        Interaction.objects.create(tenant_id=1, tenant_customer_id=customer.tenant_customer_id, interaction_type="CALL", interaction_at=now, interaction_status="پاسخ داده شد", source_system="TEST")
        Interaction.objects.create(tenant_id=1, tenant_customer_id=customer.tenant_customer_id, interaction_type="CALL", interaction_at=now-timedelta(days=30), interaction_status="کسی جواب نداد", source_system="TEST")
        context = build_management_report(SimpleNamespace(tenant_id=1), date_from=(today-timedelta(days=1)).isoformat(), date_to=today.isoformat())
        assert context["activity"]["interactions"]["total"] == 1
        assert context["activity"]["interactions"]["missed"] == 0

@pytest.mark.django_db
def test_energy_report_uses_latest_validation_run(report_tables):
    make_balance(1, 1, "100", "140")
    make_balance(1, 2, "500", "200")
    context = build_management_report(SimpleNamespace(tenant_id=1))
    assert context["energy"]["latest_run"] == 2
    assert context["energy"]["totals"]["purchase"] == Decimal("500")


@pytest.mark.django_db
def test_customer_export_is_tenant_scoped_and_contains_contact(report_tables):
    customer = make_customer(1, "صنعت یک")
    make_customer(2, "صنعت دو")
    CustomerContact.objects.create(tenant_id=1, tenant_customer=customer, contact_person="خانم رابط", phone_number="0210000", is_primary=True, data_quality_status="CLEAN")
    header, rows = export_rows(SimpleNamespace(tenant_id=1), "customers")
    assert "نام مشتری" in header
    assert len(rows) == 1
    assert rows[0][1] == "صنعت یک"
    assert rows[0][4] == "خانم رابط"


@pytest.mark.django_db
def test_contract_export_contains_volume_and_customer(report_tables):
    customer = make_customer(1, "فولاد تست")
    make_contract(customer, "R-10", "25")
    _, rows = export_rows(SimpleNamespace(tenant_id=1), "contracts")
    assert rows[0][3] == "فولاد تست"
    assert rows[0][7] == Decimal("25")


def test_unknown_export_key_is_rejected():
    with pytest.raises(ValueError):
        export_rows(SimpleNamespace(tenant_id=1), "unknown")
