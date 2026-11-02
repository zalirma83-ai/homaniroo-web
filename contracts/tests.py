from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import Http404

from customers.models import IndustrialUnit, TenantCustomer
from energy.models import EnergyType, LoadBand
from .models import Contract, ContractLine, ContractMonthlyQuantity
from .services import (
    build_contract_list,
    contract_stats,
    enrich_contracts,
    filter_contracts,
    get_contract_detail,
    contracts_for_tenant,
)


@pytest.fixture
def contract_management_tables(db):
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
        """CREATE TABLE IF NOT EXISTS core.industrial_units (
            industrial_unit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            official_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            national_id TEXT,
            economic_code TEXT,
            postal_code TEXT,
            address TEXT,
            ownership_type TEXT NOT NULL DEFAULT 'UNKNOWN',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.tenant_customers (
            tenant_customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            industrial_unit_id BIGINT NOT NULL,
            customer_status TEXT NOT NULL DEFAULT 'ACTIVE',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.counterparties (
            counterparty_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            counterparty_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            counterparty_type TEXT NOT NULL DEFAULT 'SELLER',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        """CREATE TABLE IF NOT EXISTS core.contract_monthly_quantities (
            monthly_quantity_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            contract_line_id BIGINT NOT NULL,
            period_jalali TEXT NOT NULL,
            quantity_type TEXT NOT NULL DEFAULT 'CONTRACT_DEMAND',
            quantity_value NUMERIC(30,6),
            quantity_unit TEXT NOT NULL DEFAULT 'MW',
            quantity_unit_status TEXT NOT NULL DEFAULT 'ASSUMED',
            original_value TEXT,
            source_row_id BIGINT,
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def create_catalog():
    energy, _ = EnergyType.objects.get_or_create(
        energy_type_code="THERMAL",
        defaults={"energy_type_name_fa": "برق حرارتی", "is_active": True},
    )
    band, _ = LoadBand.objects.get_or_create(
        load_band_code="BASE",
        defaults={"load_band_name_fa": "بار پایه", "is_active": True},
    )
    return energy, band


def create_customer(tenant_id, name):
    unit = IndustrialUnit.objects.create(
        official_name=name,
        normalized_name=name,
        ownership_type="UNKNOWN",
        data_quality_status="CLEAN",
    )
    return TenantCustomer.objects.create(
        tenant_id=tenant_id,
        industrial_unit=unit,
        customer_status="ACTIVE",
        data_quality_status="CLEAN",
    )


def create_sale_contract(tenant_id, customer, fingerprint, number="100"):
    return Contract.objects.create(
        tenant_id=tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        contract_direction="SALE",
        contract_number=number,
        transaction_type="BILATERAL",
        contract_status="IMPORTED",
        source_priority="PRIMARY",
        source_fingerprint=fingerprint,
        data_quality_status="CLEAN",
    )


def add_line(contract, volume=Decimal("10")):
    energy, band = create_catalog()
    return ContractLine.objects.create(
        tenant_id=contract.tenant_id,
        contract=contract,
        energy_type=energy,
        load_band=band,
        contracted_volume=volume,
        volume_unit="MW",
        volume_unit_status="CONFIRMED",
        base_rate=Decimal("500"),
        amount_value=Decimal("5000"),
        rate_unit="RIAL_KWH",
        amount_unit="RIAL",
        data_quality_status="CLEAN",
    )


@pytest.mark.django_db
def test_contract_list_and_stats_are_tenant_scoped(contract_management_tables):
    first_customer = create_customer(1, "مشتری شرکت اول")
    second_customer = create_customer(2, "مشتری شرکت دوم")
    create_sale_contract(1, first_customer, "first")
    create_sale_contract(2, second_customer, "second")

    first = SimpleNamespace(tenant_id=1)
    assert contracts_for_tenant(first).count() == 1
    assert contract_stats(first)["total"] == 1
    assert contract_stats(first)["sale"] == 1


@pytest.mark.django_db
def test_contract_search_finds_customer_name(contract_management_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_customer(1, "فولاد آزمایشی")
    contract = create_sale_contract(1, customer, "search-contract")

    result = filter_contracts(
        contracts_for_tenant(tenant), tenant, query="فولاد آزمایشی"
    )

    assert list(result.values_list("contract_id", flat=True)) == [contract.contract_id]


@pytest.mark.django_db
def test_contract_filters_direction_quality_and_energy(contract_management_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_customer(1, "مشتری فیلتر")
    sale = create_sale_contract(1, customer, "sale-filter")
    # CLEAN in the URL means no live warnings, not a raw source flag.
    from django.utils import timezone
    sale.contract_start_gregorian = timezone.localdate()
    sale.contract_end_gregorian = timezone.localdate()
    sale.save()
    add_line(sale)
    Contract.objects.create(
        tenant_id=1,
        counterparty_id=None,
        contract_direction="PURCHASE",
        transaction_type="UNKNOWN",
        contract_status="IMPORTED",
        source_priority="PRIMARY",
        source_fingerprint="purchase-filter",
        data_quality_status="REVIEW_REQUIRED",
    )
    energy = EnergyType.objects.get(energy_type_code="THERMAL")

    result = filter_contracts(
        contracts_for_tenant(tenant),
        tenant,
        direction="SALE",
        quality="CLEAN",
        energy_type=str(energy.energy_type_id),
    )

    assert result.get().contract_id == sale.contract_id


@pytest.mark.django_db
def test_enriched_sale_contract_contains_customer_and_energy(contract_management_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_customer(1, "مشتری غنی‌شده")
    contract = create_sale_contract(1, customer, "enriched")
    add_line(contract, Decimal("25"))

    row = enrich_contracts(tenant, [contract])[0]

    assert row["party_name"] == "مشتری غنی‌شده"
    assert row["energy_labels"] == ["برق حرارتی"]
    assert row["direction_label"] == "فروش"


@pytest.mark.django_db
def test_contract_detail_includes_monthly_quantities(contract_management_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_customer(1, "مشتری ماهانه")
    contract = create_sale_contract(1, customer, "monthly")
    line = add_line(contract)
    ContractMonthlyQuantity.objects.create(
        tenant_id=1,
        contract_line=line,
        period_jalali="1405/01",
        quantity_type="CONTRACT_DEMAND",
        quantity_value=Decimal("12"),
        quantity_unit="MW",
        quantity_unit_status="CONFIRMED",
        data_quality_status="CLEAN",
    )

    context = get_contract_detail(tenant, contract.contract_id)

    quantities = context["row"]["lines"][0]["monthly_quantities"]
    assert len(quantities) == 1
    assert quantities[0].period_jalali == "1405/01"


@pytest.mark.django_db
def test_contract_detail_rejects_other_tenant(contract_management_tables):
    customer = create_customer(1, "قرارداد محرمانه")
    contract = create_sale_contract(1, customer, "tenant-private")

    with pytest.raises(Http404):
        get_contract_detail(SimpleNamespace(tenant_id=2), contract.contract_id)


@pytest.mark.django_db
def test_contract_list_context_reports_missing_dates(contract_management_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_customer(1, "مشتری تاریخ ناقص")
    create_sale_contract(1, customer, "missing-date")

    context = build_contract_list(tenant)

    assert context["stats"]["missing_dates"] == 1
    assert context["filters"]["ordering"] == "newest"
