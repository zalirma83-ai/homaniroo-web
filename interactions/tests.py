from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import Http404
from django.utils import timezone

from customers.models import CustomerContact, IndustrialUnit, TenantCustomer
from .forms import InteractionCreateForm
from .models import FollowUp, Interaction
from .services import (
    build_work_queue,
    close_follow_up,
    record_interaction,
    reschedule_follow_up,
)


@pytest.fixture
def contact_followup_tables(db):
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


def make_customer(tenant_id, name, status="LEAD"):
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


def operator():
    return SimpleNamespace(
        username="operator",
        get_full_name=lambda: "اپراتور تست",
    )


@pytest.mark.django_db
def test_interaction_form_only_lists_selected_tenant_customers(
    contact_followup_tables,
):
    first = make_customer(1, "مشتری شرکت اول")
    make_customer(2, "مشتری شرکت دوم")

    form = InteractionCreateForm(tenant=SimpleNamespace(tenant_id=1))
    values = {value for value, _ in form.fields["tenant_customer_id"].choices}

    assert values == {str(first.tenant_customer_id)}


@pytest.mark.django_db
def test_interaction_form_requires_date_for_follow_up(contact_followup_tables):
    customer = make_customer(1, "مشتری پیگیری")
    form = InteractionCreateForm(
        tenant=SimpleNamespace(tenant_id=1),
        data={
            "tenant_customer_id": customer.tenant_customer_id,
            "interaction_type": "CALL",
            "interaction_status": "FOLLOW_UP_REQUIRED",
            "notes": "تماس مجدد لازم است",
            "follow_up_jalali": "",
        },
    )

    assert not form.is_valid()
    assert "follow_up_jalali" in form.errors


@pytest.mark.django_db
def test_record_interaction_creates_open_follow_up(contact_followup_tables):
    customer = make_customer(1, "مشتری تماس")
    due_at = timezone.now() + timedelta(days=2)

    interaction = record_interaction(
        tenant=SimpleNamespace(tenant_id=1),
        user=operator(),
        cleaned_data={
            "tenant_customer_id": customer.tenant_customer_id,
            "interaction_type": "CALL",
            "interaction_status": "FOLLOW_UP_REQUIRED",
            "notes": "پیشنهاد ارسال شد",
            "follow_up_jalali": due_at,
            "follow_up_reason": "بررسی پیشنهاد",
        },
    )

    follow_up = FollowUp.objects.get(interaction_id=interaction.interaction_id)
    assert interaction.operator_name_original == "اپراتور تست"
    assert follow_up.follow_up_status == "OPEN"
    assert follow_up.reason == "بررسی پیشنهاد"


@pytest.mark.django_db
def test_record_interaction_rejects_customer_of_another_tenant(
    contact_followup_tables,
):
    customer = make_customer(2, "مشتری شرکت دوم")

    with pytest.raises(Http404):
        record_interaction(
            tenant=SimpleNamespace(tenant_id=1),
            user=operator(),
            cleaned_data={
                "tenant_customer_id": customer.tenant_customer_id,
                "interaction_type": "CALL",
                "interaction_status": "NO_ANSWER",
                "notes": "",
                "follow_up_jalali": None,
                "follow_up_reason": "",
            },
        )


@pytest.mark.django_db
def test_work_queue_has_tenant_scoped_daily_buckets(contact_followup_tables):
    tenant = SimpleNamespace(tenant_id=1)
    customer = make_customer(1, "مشتری کارتابل")
    other = make_customer(2, "مشتری شرکت دیگر")
    CustomerContact.objects.create(
        tenant_id=1,
        tenant_customer=customer,
        contact_person="مسئول انرژی",
        mobile_number="09120000000",
        is_primary=True,
    )
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    values = [
        today_start - timedelta(hours=1),
        today_start + timedelta(hours=10),
        today_start + timedelta(days=3),
        None,
    ]
    for due_at in values:
        FollowUp.objects.create(
            tenant_id=1,
            tenant_customer_id=customer.tenant_customer_id,
            due_at=due_at,
            follow_up_status="OPEN",
            reason="پیگیری تست",
        )
    FollowUp.objects.create(
        tenant_id=2,
        tenant_customer_id=other.tenant_customer_id,
        due_at=today_start,
        follow_up_status="OPEN",
    )

    context = build_work_queue(tenant)

    assert context["stats"] == {
        "open": 4,
        "overdue": 1,
        "today": 1,
        "upcoming": 1,
        "without_date": 1,
    }
    assert len(context["rows"]) == 4
    assert {row["due_label"] for row in context["rows"]} == {
        "عقب‌افتاده", "امروز", "آینده", "بدون تاریخ"
    }


@pytest.mark.django_db
def test_work_queue_counts_normalized_and_legacy_missed_calls(
    contact_followup_tables,
):
    customer = make_customer(1, "مشتری بی‌پاسخ")
    for status in ("NO_ANSWER", "کسی جواب نداد", "پاسخ داده شد"):
        Interaction.objects.create(
            tenant_id=1,
            tenant_customer_id=customer.tenant_customer_id,
            interaction_type="CALL",
            interaction_at=timezone.now(),
            interaction_status=status,
            source_system="TEST",
        )

    context = build_work_queue(SimpleNamespace(tenant_id=1))

    assert context["interaction_stats"]["total"] == 3
    assert context["interaction_stats"]["missed"] == 2


@pytest.mark.django_db
def test_complete_follow_up_is_tenant_scoped_and_writes_audit_interaction(
    contact_followup_tables,
):
    customer = make_customer(1, "مشتری اقدام‌شده")
    follow_up = FollowUp.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        due_at=timezone.now(),
        follow_up_status="OPEN",
        reason="تماس مجدد",
    )

    with pytest.raises(Http404):
        close_follow_up(
            tenant=SimpleNamespace(tenant_id=2),
            follow_up_id=follow_up.follow_up_id,
            user=operator(),
            action="DONE",
        )

    close_follow_up(
        tenant=SimpleNamespace(tenant_id=1),
        follow_up_id=follow_up.follow_up_id,
        user=operator(),
        action="DONE",
        note="تماس انجام شد",
    )
    follow_up.refresh_from_db()
    audit = Interaction.objects.get(source_record_id=f"follow_up:{follow_up.follow_up_id}")
    assert follow_up.follow_up_status == "COMPLETED"
    assert audit.interaction_status == "COMPLETED"
    assert audit.notes == "تماس انجام شد"


@pytest.mark.django_db
def test_reschedule_follow_up_updates_due_date_and_logs_action(
    contact_followup_tables,
):
    customer = make_customer(1, "مشتری تغییر زمان")
    follow_up = FollowUp.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        due_at=timezone.now(),
        follow_up_status="OPEN",
        reason="زمان قبلی",
    )
    new_due = timezone.now() + timedelta(days=5)

    reschedule_follow_up(
        tenant=SimpleNamespace(tenant_id=1),
        follow_up_id=follow_up.follow_up_id,
        user=operator(),
        due_at=new_due,
        reason="درخواست مشتری",
    )

    follow_up.refresh_from_db()
    audit = Interaction.objects.get(source_record_id=f"follow_up:{follow_up.follow_up_id}")
    assert follow_up.reason == "درخواست مشتری"
    assert audit.interaction_status == "RESCHEDULED"

# آزمون‌های گام ۴.۱۱ ـ مرکز هشدارها
from contracts.models import Contract
from .alert_services import (
    alert_quality_summary,
    build_alert_center,
    synchronize_alerts,
    update_alert_status,
)
from .models import Alert


@pytest.fixture
def alert_system_tables(contact_followup_tables):
    statements = [
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
        """CREATE TABLE IF NOT EXISTS core.alerts (
            alert_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT,
            follow_up_id BIGINT,
            contract_id BIGINT,
            alert_type TEXT NOT NULL,
            alert_at TIMESTAMP,
            severity TEXT NOT NULL CHECK (severity IN ('NORMAL', 'IMPORTANT', 'CRITICAL')),
            alert_status TEXT NOT NULL CHECK (alert_status IN ('OPEN', 'SEEN', 'DISMISSED', 'COMPLETED')),
            message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


def make_contract(customer, fingerprint, end_date):
    return Contract.objects.create(
        tenant_id=customer.tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        contract_direction="SALE",
        contract_number=fingerprint,
        contract_end_gregorian=end_date,
        source_fingerprint=fingerprint,
    )


@pytest.mark.django_db
def test_sync_creates_follow_up_alerts_by_due_state(alert_system_tables):
    customer = make_customer(1, "مشتری هشدار پیگیری")
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    for due_at in (
        today_start - timedelta(days=1),
        today_start + timedelta(days=4),
        today_start + timedelta(days=20),
        None,
    ):
        FollowUp.objects.create(
            tenant_id=1,
            tenant_customer_id=customer.tenant_customer_id,
            due_at=due_at,
            follow_up_status="OPEN",
        )

    synchronize_alerts(SimpleNamespace(tenant_id=1))

    alerts = Alert.objects.filter(alert_type="FOLLOW_UP")
    assert alerts.count() == 3
    assert set(alerts.values_list("severity", flat=True)) == {
        "CRITICAL", "IMPORTANT", "NORMAL"
    }


@pytest.mark.django_db
def test_sync_contract_alerts_require_real_end_dates(alert_system_tables):
    customer = make_customer(1, "مشتری قرارداد")
    today = timezone.localdate()
    make_contract(customer, "missing-date", None)
    make_contract(customer, "expired", today - timedelta(days=1))
    make_contract(customer, "ending", today + timedelta(days=5))
    make_contract(customer, "future", today + timedelta(days=45))

    synchronize_alerts(SimpleNamespace(tenant_id=1))

    assert Alert.objects.filter(alert_type="CONTRACT_EXPIRED").count() == 1
    assert Alert.objects.filter(alert_type="CONTRACT_ENDING").count() == 1
    assert alert_quality_summary(SimpleNamespace(tenant_id=1))[
        "contracts_missing_end_date"
    ] == 1


@pytest.mark.django_db
def test_sync_creates_no_contact_and_latest_missed_call_alerts(
    alert_system_tables,
):
    never = make_customer(1, "بدون تماس")
    missed = make_customer(1, "آخرین تماس بی‌پاسخ")
    answered = make_customer(1, "تماس پاسخ‌داده‌شده")
    now = timezone.now()
    Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=missed.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=now,
        interaction_status="کسی جواب نداد",
        source_system="TEST",
    )
    Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=answered.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=now - timedelta(hours=2),
        interaction_status="NO_ANSWER",
        source_system="TEST",
    )
    Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=answered.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=now,
        interaction_status="INTERESTED",
        source_system="TEST",
    )

    synchronize_alerts(SimpleNamespace(tenant_id=1))

    assert Alert.objects.filter(
        alert_type="CUSTOMER_NO_CONTACT",
        tenant_customer_id=never.tenant_customer_id,
    ).exists()
    assert Alert.objects.filter(
        alert_type="MISSED_CALL",
        tenant_customer_id=missed.tenant_customer_id,
    ).exists()
    assert not Alert.objects.filter(
        alert_type="MISSED_CALL",
        tenant_customer_id=answered.tenant_customer_id,
    ).exists()


@pytest.mark.django_db
def test_alert_sync_is_tenant_scoped(alert_system_tables):
    first = make_customer(1, "شرکت اول")
    second = make_customer(2, "شرکت دوم")

    synchronize_alerts(SimpleNamespace(tenant_id=1))

    assert Alert.objects.filter(
        tenant_id=1, tenant_customer_id=first.tenant_customer_id
    ).exists()
    assert not Alert.objects.filter(
        tenant_id=2, tenant_customer_id=second.tenant_customer_id
    ).exists()


@pytest.mark.django_db
def test_alert_sync_is_idempotent(alert_system_tables):
    make_customer(1, "هشدار بدون تکرار")
    tenant = SimpleNamespace(tenant_id=1)

    synchronize_alerts(tenant)
    first_count = Alert.objects.count()
    synchronize_alerts(tenant)

    assert Alert.objects.count() == first_count


@pytest.mark.django_db
def test_stale_no_contact_alert_is_resolved(alert_system_tables):
    customer = make_customer(1, "مشتری تماس‌گرفته‌شده")
    tenant = SimpleNamespace(tenant_id=1)
    synchronize_alerts(tenant)
    alert = Alert.objects.get(alert_type="CUSTOMER_NO_CONTACT")
    Interaction.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_type="CALL",
        interaction_at=timezone.now(),
        interaction_status="INTERESTED",
        source_system="TEST",
    )

    synchronize_alerts(tenant)

    alert.refresh_from_db()
    assert alert.alert_status == "COMPLETED"


@pytest.mark.django_db
def test_alert_status_change_rejects_other_tenant(alert_system_tables):
    customer = make_customer(1, "هشدار شرکت اول")
    alert = Alert.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        alert_type="CUSTOMER_NO_CONTACT",
        severity="NORMAL",
        alert_status="OPEN",
    )

    with pytest.raises(Http404):
        update_alert_status(
            tenant=SimpleNamespace(tenant_id=2),
            alert_id=alert.alert_id,
            status="COMPLETED",
        )

    update_alert_status(
        tenant=SimpleNamespace(tenant_id=1),
        alert_id=alert.alert_id,
        status="SEEN",
    )
    alert.refresh_from_db()
    assert alert.alert_status == "SEEN"


@pytest.mark.django_db
def test_alert_center_filters_and_counts_active_alerts(alert_system_tables):
    customer = make_customer(1, "مرکز هشدار")
    Alert.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        alert_type="MISSED_CALL",
        severity="IMPORTANT",
        alert_status="OPEN",
        message="آخرین تماس بی‌پاسخ بود",
    )
    Alert.objects.create(
        tenant_id=1,
        tenant_customer_id=customer.tenant_customer_id,
        alert_type="CUSTOMER_NO_CONTACT",
        severity="NORMAL",
        alert_status="COMPLETED",
    )

    context = build_alert_center(
        SimpleNamespace(tenant_id=1),
        status="active",
        alert_group="missed-call",
    )

    assert context["stats"]["open"] == 1
    assert context["stats"]["warning"] == 1
    assert len(context["rows"]) == 1
    assert context["rows"][0]["type_label"] == "تماس بی‌پاسخ"
