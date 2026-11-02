from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import Http404
from django.utils import timezone

from interactions.models import FollowUp, Interaction
from .forms import InteractionForm
from .models import CustomerContact, IndustrialUnit, TenantCustomer
from .services import (
    convert_to_active_customer,
    create_prospective_customer,
    filter_prospective_customers,
    prospective_customers_for_tenant,
    record_customer_interaction,
)


@pytest.fixture
def prospective_customer_tables(db):
    statements = [
        "CREATE SCHEMA IF NOT EXISTS core",
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
            customer_status TEXT NOT NULL DEFAULT 'LEAD',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.customer_contacts (
            contact_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT NOT NULL,
            contact_person TEXT,
            mobile_number TEXT,
            phone_number TEXT,
            email TEXT,
            website TEXT,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
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
        )""",
        """CREATE TABLE IF NOT EXISTS core.follow_ups (
            follow_up_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT NOT NULL,
            interaction_id BIGINT,
            due_at TIMESTAMP,
            follow_up_status TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def customer_payload(name="صنایع نمونه"):
    return {
        "official_name": name,
        "national_id": "",
        "economic_code": "",
        "postal_code": "",
        "address": "تهران",
        "contact_person": "مسئول خرید",
        "mobile_number": "09120000000",
        "phone_number": "",
        "email": "",
        "website": "",
    }


@pytest.mark.django_db
def test_create_prospective_customer_builds_identity_and_contact(
    prospective_customer_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_prospective_customer(tenant=tenant, cleaned_data=customer_payload())

    assert customer.customer_status == "LEAD"
    assert customer.tenant_id == 1
    assert customer.industrial_unit.official_name == "صنایع نمونه"
    contact = CustomerContact.objects.get(tenant_customer=customer)
    assert contact.mobile_number == "09120000000"
    assert contact.is_primary is True


@pytest.mark.django_db
def test_prospective_list_and_search_are_tenant_scoped(prospective_customer_tables):
    first = SimpleNamespace(tenant_id=1)
    second = SimpleNamespace(tenant_id=2)
    create_prospective_customer(
        tenant=first, cleaned_data=customer_payload("شرکت آفتاب")
    )
    create_prospective_customer(
        tenant=second, cleaned_data=customer_payload("شرکت مهتاب")
    )

    first_queryset = prospective_customers_for_tenant(first)
    result = filter_prospective_customers(first_queryset, query="آفتاب")

    assert first_queryset.count() == 1
    assert result.get().industrial_unit.official_name == "شرکت آفتاب"
    assert prospective_customers_for_tenant(second).count() == 1


@pytest.mark.django_db
def test_duplicate_prospective_customer_is_rejected_in_same_tenant(
    prospective_customer_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    create_prospective_customer(tenant=tenant, cleaned_data=customer_payload())

    with pytest.raises(ValidationError):
        create_prospective_customer(tenant=tenant, cleaned_data=customer_payload())


@pytest.mark.django_db
def test_record_interaction_creates_jalali_follow_up(prospective_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_prospective_customer(tenant=tenant, cleaned_data=customer_payload())
    form = InteractionForm(
        data={
            "interaction_type": "CALL",
            "interaction_status": "FOLLOW_UP_REQUIRED",
            "notes": "ارسال پیشنهاد قیمت",
            "follow_up_jalali": "۱۴۰۵/۰۸/۱۵ ۱۰:۳۰",
            "follow_up_reason": "بررسی پیشنهاد",
        }
    )
    assert form.is_valid(), form.errors
    user = SimpleNamespace(username="operator", get_full_name=lambda: "اپراتور تست")

    interaction = record_customer_interaction(
        tenant=tenant, customer=customer, user=user, cleaned_data=form.cleaned_data
    )

    assert interaction.interaction_status == "FOLLOW_UP_REQUIRED"
    follow_up = FollowUp.objects.get(interaction_id=interaction.interaction_id)
    assert follow_up.follow_up_status == "OPEN"
    assert follow_up.reason == "بررسی پیشنهاد"


@pytest.mark.django_db
def test_convert_to_active_customer_preserves_tenant_isolation(
    prospective_customer_tables,
):
    first = SimpleNamespace(tenant_id=1)
    second = SimpleNamespace(tenant_id=2)
    customer = create_prospective_customer(tenant=first, cleaned_data=customer_payload())

    with pytest.raises(Http404):
        convert_to_active_customer(tenant=second, customer=customer)

    converted = convert_to_active_customer(tenant=first, customer=customer)
    converted.refresh_from_db()
    assert converted.customer_status == "ACTIVE"
    assert prospective_customers_for_tenant(first).count() == 0

# آزمون‌های گام ۴.۷ ـ مشترکین فعال
from decimal import Decimal

from contracts.models import Contract, ContractLine, ContractMonthlyQuantity
from energy.models import EnergyType, LoadBand
from .services import (
    active_contract_rows,
    active_customers_for_tenant,
    filter_active_customers,
    get_active_customer,
)


@pytest.fixture
def active_customer_tables(prospective_customer_tables):
    statements = [
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
            contracted_volume NUMERIC,
            volume_unit TEXT NOT NULL DEFAULT 'MW',
            volume_unit_status TEXT NOT NULL DEFAULT 'ASSUMED',
            base_rate NUMERIC,
            corrected_rate NUMERIC,
            rate_1401 NUMERIC,
            adjusted_rate_1401 NUMERIC,
            adjusted_rate_1402 NUMERIC,
            monthly_rate NUMERIC,
            rate_unit TEXT NOT NULL DEFAULT 'UNKNOWN',
            amount_value NUMERIC,
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
            quantity_value NUMERIC,
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


def create_active_customer(tenant_id, name, status="ACTIVE"):
    unit = IndustrialUnit.objects.create(
        official_name=name,
        normalized_name=name,
        ownership_type="UNKNOWN",
        data_quality_status="REVIEW_REQUIRED",
    )
    return TenantCustomer.objects.create(
        tenant_id=tenant_id,
        industrial_unit=unit,
        customer_status=status,
        data_quality_status="REVIEW_REQUIRED",
    )


def attach_contract(customer, fingerprint="sale-1", volume=None, amount=None):
    energy_type, _ = EnergyType.objects.get_or_create(
        energy_type_code="FREE",
        defaults={"energy_type_name_fa": "برق آزاد", "description": "آزاد"},
    )
    load_band, _ = LoadBand.objects.get_or_create(
        load_band_code="BASE",
        defaults={"load_band_name_fa": "بار پایه", "description": "پایه"},
    )
    contract = Contract.objects.create(
        tenant_id=customer.tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        contract_direction="SALE",
        source_fingerprint=fingerprint,
    )
    line = ContractLine.objects.create(
        tenant_id=customer.tenant_id,
        contract=contract,
        energy_type=energy_type,
        load_band=load_band,
        contracted_volume=volume,
        amount_value=amount,
    )
    return contract, line


@pytest.mark.django_db
def test_active_customer_list_is_tenant_scoped(active_customer_tables):
    first = SimpleNamespace(tenant_id=1)
    second = SimpleNamespace(tenant_id=2)
    create_active_customer(1, "مشترک فعال اول")
    create_active_customer(2, "مشترک فعال دوم")
    create_active_customer(1, "مشتری بالقوه", status="LEAD")

    assert active_customers_for_tenant(first).count() == 1
    assert active_customers_for_tenant(second).count() == 1
    assert active_customers_for_tenant(first).get().industrial_unit.official_name == "مشترک فعال اول"


@pytest.mark.django_db
def test_active_customer_volume_filter(active_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    with_volume = create_active_customer(1, "دارای حجم")
    without_volume = create_active_customer(1, "بدون حجم")
    attach_contract(with_volume, "with-volume", volume=Decimal("12.5"))
    attach_contract(without_volume, "without-volume")

    queryset = active_customers_for_tenant(tenant)
    result = filter_active_customers(queryset, contract_data="with_volume")

    assert result.count() == 1
    assert result.get().tenant_customer_id == with_volume.tenant_customer_id


@pytest.mark.django_db
def test_active_customer_contract_rows_include_energy_and_monthly_data(active_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "صنایع انرژی")
    contract, line = attach_contract(
        customer, "detailed-sale", volume=Decimal("20"), amount=Decimal("500000")
    )
    ContractMonthlyQuantity.objects.create(
        tenant_id=1,
        contract_line=line,
        period_jalali="1405/01",
        quantity_value=Decimal("10"),
    )

    data = active_contract_rows(tenant, customer)

    assert data["contract_count"] == 1
    assert data["contracts_missing_dates"] == 1
    assert data["known_volume_total"] == Decimal("20")
    assert data["rows"][0]["lifecycle_label"] == "تاریخ نامشخص"
    assert data["rows"][0]["lines"][0]["item"].energy_type.energy_type_name_fa == "برق آزاد"
    assert len(data["rows"][0]["lines"][0]["monthly_quantities"]) == 1


@pytest.mark.django_db
def test_active_customer_detail_rejects_other_tenant(active_customer_tables):
    customer = create_active_customer(1, "مشترک محرمانه")

    with pytest.raises(Http404):
        get_active_customer(SimpleNamespace(tenant_id=2), customer.tenant_customer_id)


@pytest.mark.django_db
def test_active_customer_search_uses_contact_information(active_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "شرکت نمونه")
    CustomerContact.objects.create(
        tenant_id=1,
        tenant_customer=customer,
        contact_person="مسئول انرژی ویژه",
        mobile_number="09121112233",
        is_primary=True,
    )

    result = filter_active_customers(
        active_customers_for_tenant(tenant), query="09121112233"
    )

    assert result.count() == 1
    assert result.get().tenant_customer_id == customer.tenant_customer_id

# آزمون‌های گام ۴.۸ ـ مشترکین راکد
from .forms import DormantRestoreForm, DormantTransitionForm
from .models import CustomerStatusHistory
from .services import (
    dormant_customers_for_tenant,
    mark_customer_dormant,
    restore_dormant_customer,
)


def audit_user():
    return SimpleNamespace(
        pk=7,
        username="senior_operator",
        get_full_name=lambda: "اپراتور ارشد",
    )


@pytest.mark.django_db
def test_mark_active_customer_dormant_records_reason(prospective_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "مشترک متوقف‌شده")

    changed = mark_customer_dormant(
        tenant=tenant,
        customer_id=customer.tenant_customer_id,
        user=audit_user(),
        reason_code="CONTRACT_ENDED",
        reason_note="قرارداد تمدید نشد",
    )

    changed.refresh_from_db()
    history = CustomerStatusHistory.objects.get(
        tenant_customer_id=customer.tenant_customer_id
    )
    assert changed.customer_status == "DORMANT"
    assert history.previous_status == "ACTIVE"
    assert history.new_status == "DORMANT"
    assert history.reason_code == "CONTRACT_ENDED"
    assert history.changed_by_name == "اپراتور ارشد"


@pytest.mark.django_db
def test_restore_dormant_customer_to_lead(prospective_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "مشتری بازگشتی")
    mark_customer_dormant(
        tenant=tenant,
        customer_id=customer.tenant_customer_id,
        user=audit_user(),
        reason_code="NO_INTEREST",
    )

    restored = restore_dormant_customer(
        tenant=tenant,
        customer_id=customer.tenant_customer_id,
        user=audit_user(),
        target_status="LEAD",
        reason_note="درخواست مذاکره مجدد",
    )

    restored.refresh_from_db()
    assert restored.customer_status == "LEAD"
    latest = CustomerStatusHistory.objects.filter(
        tenant_customer_id=customer.tenant_customer_id
    ).first()
    assert latest.previous_status == "DORMANT"
    assert latest.new_status == "LEAD"
    assert latest.reason_code == "REACTIVATED"


@pytest.mark.django_db
def test_dormant_change_rejects_other_tenant(prospective_customer_tables):
    customer = create_active_customer(1, "مشتری شرکت اول")

    with pytest.raises(Http404):
        mark_customer_dormant(
            tenant=SimpleNamespace(tenant_id=2),
            customer_id=customer.tenant_customer_id,
            user=audit_user(),
            reason_code="OTHER",
            reason_note="تست",
        )


@pytest.mark.django_db
def test_dormant_list_includes_reason_and_last_interaction(prospective_customer_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "مشترک دارای سابقه")
    Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=timezone.now(),
        interaction_status="NO_ANSWER",
        source_system="TEST",
    )
    mark_customer_dormant(
        tenant=tenant,
        customer_id=customer.tenant_customer_id,
        user=audit_user(),
        reason_code="NO_RESPONSE",
        reason_note="سه تماس بدون پاسخ",
    )

    row = dormant_customers_for_tenant(tenant).get()

    assert row.dormant_reason_code == "NO_RESPONSE"
    assert row.dormant_reason_note == "سه تماس بدون پاسخ"
    assert row.previous_customer_status == "ACTIVE"
    assert row.last_interaction_at is not None


@pytest.mark.django_db
def test_dormant_forms_validate_reason_and_destination():
    other = DormantTransitionForm(data={"reason_code": "OTHER", "reason_note": ""})
    assert not other.is_valid()
    assert "reason_note" in other.errors

    restore = DormantRestoreForm(
        data={"target_status": "ACTIVE", "reason_note": "تمدید همکاری"}
    )
    assert restore.is_valid(), restore.errors

# آزمون‌های گام ۴.۹ ـ پرونده کامل مشتری
from .models import BillingAccount, IndustrialUnitAlias, Meter, MeterReading
from .services import build_customer_profile, get_customer_for_profile


@pytest.fixture
def customer_profile_tables(active_customer_tables):
    statements = [
        """CREATE TABLE IF NOT EXISTS core.industrial_unit_aliases (
            industrial_unit_alias_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            industrial_unit_id BIGINT NOT NULL,
            alias_name TEXT NOT NULL,
            normalized_alias_name TEXT NOT NULL,
            source_row_id BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS core.billing_accounts (
            billing_account_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT NOT NULL,
            bill_id TEXT NOT NULL,
            consumer_code TEXT,
            regional_company TEXT,
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.meters (
            meter_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT NOT NULL,
            meter_number TEXT,
            meter_type TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING_DATA',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.meter_readings (
            meter_reading_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            meter_id BIGINT NOT NULL,
            reading_at TIMESTAMPTZ NOT NULL,
            consumption_value NUMERIC,
            consumption_unit TEXT,
            source_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


@pytest.mark.django_db
def test_customer_profile_is_available_for_any_status_and_tenant_scoped(
    customer_profile_tables,
):
    first = create_active_customer(1, "پرونده شرکت اول", status="LEAD")
    second = create_active_customer(2, "پرونده شرکت دوم", status="ACTIVE")

    found = get_customer_for_profile(
        SimpleNamespace(tenant_id=1), first.tenant_customer_id
    )
    assert found.customer_status == "LEAD"

    with pytest.raises(Http404):
        get_customer_for_profile(
            SimpleNamespace(tenant_id=1), second.tenant_customer_id
        )


@pytest.mark.django_db
def test_customer_profile_collects_identity_contact_and_billing(
    customer_profile_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "صنایع یکپارچه")
    IndustrialUnitAlias.objects.create(
        industrial_unit=customer.industrial_unit,
        alias_name="نام تجاری صنایع یکپارچه",
        normalized_alias_name="نام تجاری صنایع یکپارچه",
        source_row_id=1,
    )
    BillingAccount.objects.create(
        tenant_id=1,
        tenant_customer=customer,
        bill_id="123456789",
        regional_company="برق منطقه‌ای تهران",
    )
    CustomerContact.objects.create(
        tenant_id=1,
        tenant_customer=customer,
        contact_person="مسئول انرژی",
        mobile_number="09120000000",
    )

    profile = build_customer_profile(tenant, customer)

    assert profile["summary"]["contact_count"] == 1
    assert profile["summary"]["billing_account_count"] == 1
    assert profile["aliases"][0].alias_name == "نام تجاری صنایع یکپارچه"
    assert profile["billing_accounts"][0].bill_id == "123456789"


@pytest.mark.django_db
def test_customer_profile_includes_purchase_and_sale_contracts(
    customer_profile_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "مشترک دوطرفه")
    _, sale_line = attach_contract(customer, "profile-sale", volume=Decimal("15"))
    purchase = Contract.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        contract_direction="PURCHASE",
        source_fingerprint="profile-purchase",
    )
    ContractLine.objects.create(
        tenant_id=1,
        contract=purchase,
        energy_type=sale_line.energy_type,
        load_band=sale_line.load_band,
        contracted_volume=Decimal("20"),
    )

    profile = build_customer_profile(tenant, customer)

    assert profile["contract_data"]["contract_count"] == 2
    assert profile["contract_data"]["sale_contracts"] == 1
    assert profile["contract_data"]["purchase_contracts"] == 1
    assert profile["contract_data"]["known_volume_total"] == Decimal("35")


@pytest.mark.django_db
def test_customer_profile_collects_interactions_followups_and_status_history(
    customer_profile_tables,
):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "پرونده ارتباطات")
    interaction = Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=timezone.now(),
        interaction_status="FOLLOW_UP_REQUIRED",
        source_system="TEST",
    )
    FollowUp.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_id=interaction.interaction_id,
        due_at=timezone.now(),
        follow_up_status="OPEN",
        reason="تماس مجدد",
    )
    mark_customer_dormant(
        tenant=tenant,
        customer_id=customer.tenant_customer_id,
        user=audit_user(),
        reason_code="NO_RESPONSE",
    )
    customer.refresh_from_db()

    profile = build_customer_profile(tenant, customer)

    assert profile["summary"]["interaction_count"] == 1
    assert profile["summary"]["open_follow_up_count"] == 1
    assert profile["summary"]["status_change_count"] == 1
    assert profile["status_label"] == "مشترک راکد"


@pytest.mark.django_db
def test_customer_profile_collects_meter_readings(customer_profile_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = create_active_customer(1, "مشترک دارای کنتور")
    meter = Meter.objects.create(
        tenant_id=1,
        tenant_customer=customer,
        meter_number="M-100",
        meter_type="دیجیتال",
        status="ACTIVE",
    )
    MeterReading.objects.create(
        tenant_id=1,
        meter=meter,
        reading_at=timezone.now(),
        consumption_value=Decimal("1250.5"),
        consumption_unit="kWh",
        source_name="TEST",
    )

    profile = build_customer_profile(tenant, customer)

    assert profile["summary"]["meter_count"] == 1
    assert profile["summary"]["reading_count"] == 1
    assert profile["meter_rows"][0]["readings"][0].consumption_value == Decimal("1250.5")
