from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from django.utils import timezone

from contracts.models import Contract, ContractLine
from .models import EnergyBalance, EnergyType, LoadBand
from .services import (
    build_energy_trading_context,
    filter_balances,
    latest_balances,
    latest_run_id,
    transaction_summary,
)


@pytest.fixture
def energy_trading_tables(db):
    statements = [
        "CREATE SCHEMA IF NOT EXISTS core",
        """CREATE TABLE IF NOT EXISTS core.energy_types (
            energy_type_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            energy_type_code TEXT NOT NULL UNIQUE,
            energy_type_name_fa TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )""",
        """CREATE TABLE IF NOT EXISTS core.load_bands (
            load_band_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            load_band_code TEXT NOT NULL UNIQUE,
            load_band_name_fa TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )""",
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
            source_priority TEXT NOT NULL DEFAULT 'PRIMARY',
            source_fingerprint TEXT NOT NULL,
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.contract_lines (
            contract_line_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            contract_id BIGINT NOT NULL,
            energy_type_id BIGINT NOT NULL,
            load_band_id BIGINT NOT NULL,
            contracted_volume NUMERIC(30,6),
            volume_unit TEXT NOT NULL DEFAULT 'MW',
            volume_unit_status TEXT NOT NULL DEFAULT 'ASSUMED',
            base_rate NUMERIC(30,6),
            corrected_rate NUMERIC(30,6),
            rate_1401 NUMERIC(30,6),
            adjusted_rate_1401 NUMERIC(30,6),
            adjusted_rate_1402 NUMERIC(30,6),
            monthly_rate NUMERIC(30,6),
            rate_unit TEXT NOT NULL DEFAULT 'UNKNOWN',
            amount_value NUMERIC(30,6),
            amount_unit TEXT NOT NULL DEFAULT 'UNKNOWN',
            source_row_id BIGINT,
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.energy_balances (
            energy_balance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            validation_run_id BIGINT NOT NULL,
            tenant_id BIGINT NOT NULL,
            balance_scope TEXT NOT NULL,
            period_jalali TEXT,
            energy_type_id BIGINT NOT NULL,
            load_band_id BIGINT NOT NULL,
            purchase_volume NUMERIC NOT NULL DEFAULT 0,
            sales_volume NUMERIC NOT NULL DEFAULT 0,
            balance_value NUMERIC NOT NULL DEFAULT 0,
            balance_status TEXT NOT NULL,
            volume_unit TEXT NOT NULL DEFAULT 'MW',
            data_completeness TEXT NOT NULL,
            calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def catalog():
    energy, _ = EnergyType.objects.get_or_create(
        energy_type_code="THERMAL",
        defaults={"energy_type_name_fa": "برق حرارتی", "is_active": True},
    )
    load, _ = LoadBand.objects.get_or_create(
        load_band_code="BASE",
        defaults={"load_band_name_fa": "بار پایه", "is_active": True},
    )
    return energy, load


def make_balance(
    tenant_id,
    run_id,
    *,
    scope="CONTRACT_TOTAL",
    period=None,
    purchase="100",
    sales="140",
    status="DEFICIT",
    completeness="COMPLETE",
):
    energy, load = catalog()
    purchase_value = Decimal(purchase)
    sales_value = Decimal(sales)
    return EnergyBalance.objects.create(
        validation_run_id=run_id,
        tenant_id=tenant_id,
        balance_scope=scope,
        period_jalali=period,
        energy_type_id=energy.energy_type_id,
        load_band_id=load.load_band_id,
        purchase_volume=purchase_value,
        sales_volume=sales_value,
        balance_value=purchase_value - sales_value,
        balance_status=status,
        volume_unit="MW",
        data_completeness=completeness,
        calculated_at=timezone.now(),
    )


def make_contract(tenant_id, direction, fingerprint, volume=None):
    energy, load = catalog()
    contract = Contract.objects.create(
        tenant_id=tenant_id,
        contract_direction=direction,
        transaction_type="UNKNOWN",
        contract_status="IMPORTED",
        source_priority="PRIMARY",
        source_fingerprint=fingerprint,
        data_quality_status="REVIEW_REQUIRED",
    )
    ContractLine.objects.create(
        tenant_id=tenant_id,
        contract=contract,
        energy_type=energy,
        load_band=load,
        contracted_volume=volume,
        volume_unit="MW",
        volume_unit_status="ASSUMED",
        rate_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        data_quality_status="REVIEW_REQUIRED",
    )
    return contract


@pytest.mark.django_db
def test_latest_run_is_tenant_scoped(energy_trading_tables):
    make_balance(1, 1)
    make_balance(1, 3)
    make_balance(2, 9)

    assert latest_run_id(SimpleNamespace(tenant_id=1)) == 3
    queryset, run_id = latest_balances(SimpleNamespace(tenant_id=1))
    assert run_id == 3
    assert queryset.count() == 1


@pytest.mark.django_db
def test_balance_filters_scope_period_and_status(energy_trading_tables):
    tenant = SimpleNamespace(tenant_id=1)
    make_balance(1, 1, scope="CONTRACT_TOTAL")
    monthly = make_balance(
        1,
        1,
        scope="MONTHLY_AVAILABLE",
        period="1405/01",
        status="DEFICIT",
        completeness="INCOMPLETE_PURCHASE",
    )
    queryset, _ = latest_balances(tenant)

    result = filter_balances(
        queryset,
        scope="MONTHLY_AVAILABLE",
        period="1405/01",
        status="DEFICIT",
        completeness="INCOMPLETE_PURCHASE",
    )

    assert result.get().energy_balance_id == monthly.energy_balance_id


@pytest.mark.django_db
def test_context_uses_contract_total_for_summary(energy_trading_tables):
    tenant = SimpleNamespace(tenant_id=1)
    make_balance(1, 1, purchase="100", sales="140")
    make_balance(
        1,
        1,
        scope="MONTHLY_AVAILABLE",
        period="1405/01",
        purchase="0",
        sales="500",
        completeness="INCOMPLETE_PURCHASE",
    )

    context = build_energy_trading_context(tenant)

    assert context["summary"]["purchase"] == Decimal("100")
    assert context["summary"]["sales"] == Decimal("140")
    assert context["summary"]["balance_label"] == "کسری"


@pytest.mark.django_db
def test_monthly_series_is_ordered_and_reports_incomplete_rows(
    energy_trading_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    make_balance(
        1, 1, scope="MONTHLY_AVAILABLE", period="1405/02",
        purchase="0", sales="20", completeness="INCOMPLETE_PURCHASE",
    )
    make_balance(
        1, 1, scope="MONTHLY_AVAILABLE", period="1405/01",
        purchase="10", sales="15", completeness="COMPLETE",
    )

    series = build_energy_trading_context(tenant)["monthly_series"]

    assert [item["period_jalali"] for item in series] == ["1405/01", "1405/02"]
    assert series[1]["incomplete"] == 1
    assert series[1]["sales_width"] == 100


@pytest.mark.django_db
def test_transaction_summary_is_tenant_scoped(energy_trading_tables):
    make_contract(1, "PURCHASE", "purchase-first", Decimal("50"))
    make_contract(1, "SALE", "sale-first", None)
    make_contract(2, "SALE", "sale-second", Decimal("900"))

    result = transaction_summary(SimpleNamespace(tenant_id=1))

    assert result["purchase"]["contracts"] == 1
    assert result["purchase"]["known_volume"] == Decimal("50")
    assert result["sale"]["contracts"] == 1
    assert result["sale"]["missing_volume"] == 1


@pytest.mark.django_db
def test_rows_include_energy_and_load_labels(energy_trading_tables):
    tenant = SimpleNamespace(tenant_id=1)
    make_balance(1, 1)

    context = build_energy_trading_context(tenant)
    row = context["rows"][0]

    assert row["energy_type"].energy_type_name_fa == "برق حرارتی"
    assert row["load_band"].load_band_name_fa == "بار پایه"
    assert row["status_label"] == "کسری"


@pytest.mark.django_db
def test_empty_tenant_has_safe_empty_context(energy_trading_tables):
    context = build_energy_trading_context(SimpleNamespace(tenant_id=77))

    assert context["latest_run"] is None
    assert context["summary"]["rows"] == 0
    assert context["summary"]["balance_label"] == "فاقد داده"
    assert context["rows"] == []
