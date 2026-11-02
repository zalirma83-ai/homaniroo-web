import re
from dataclasses import dataclass
from decimal import Decimal

import jdatetime
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone

from auditlog.services import record_audit_event
from contracts.models import Contract, ContractLine, ContractMonthlyQuantity
from common.information_status import (
    assess_contract, assess_customer, customer_information_queryset, filter_information,
)
from interactions.models import FollowUp, Interaction
from .models import (
    BillingAccount,
    CustomerContact,
    CustomerStatusHistory,
    IndustrialUnit,
    IndustrialUnitAlias,
    Meter,
    MeterReading,
    TenantCustomer,
)


STATUS_LABELS = {
    "INITIAL_CONTACT": "تماس اولیه انجام شد",
    "NO_ANSWER": "پاسخ نداد",
    "INTERESTED": "علاقه‌مند به ادامه مذاکره",
    "FOLLOW_UP_REQUIRED": "نیازمند پیگیری مجدد",
    "NOT_INTERESTED": "عدم تمایل",
    "WRONG_NUMBER": "شماره اشتباه",
    "CONTRACTED_ELSEWHERE": "دارای قرارداد با مجموعه دیگر",
    "OTHER": "سایر",
}
TYPE_LABELS = {
    "CALL": "تماس تلفنی",
    "MEETING": "جلسه",
    "MESSAGE": "پیام‌رسان",
    "EMAIL": "ایمیل",
    "OTHER": "سایر",
}
DORMANT_REASON_LABELS = {
    "CONTRACT_ENDED": "پایان قرارداد یا دوره همکاری",
    "NO_INTEREST": "عدم تمایل به ادامه همکاری",
    "NO_RESPONSE": "عدم پاسخ‌گویی یا قطع ارتباط",
    "OTHER_PROVIDER": "همکاری با تأمین‌کننده دیگر",
    "ACTIVITY_SUSPENDED": "توقف یا تعلیق فعالیت مشترک",
    "FINANCIAL_ISSUE": "مسائل مالی یا بدهی",
    "OTHER": "سایر موارد",
    "REACTIVATED": "بازگشت مجدد به چرخه همکاری",
}
CUSTOMER_HISTORY_REASON_LABELS = {
    **DORMANT_REASON_LABELS,
    "LEAD_CONVERTED": "تبدیل مشترک بالقوه به فعال",
}
CUSTOMER_STATUS_LABELS = {
    "LEAD": "مشترک بالقوه",
    "ACTIVE": "مشترک فعال",
    "DORMANT": "مشترک راکد",
}


def normalize_company_name(value):
    value = (value or "").strip().lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = re.sub(r"[\u200c\s]+", " ", value)
    value = re.sub(r"[^0-9a-zآ-ی ]+", "", value)
    return value.strip()


def prospective_customers_for_tenant(tenant):
    if tenant is None:
        return TenantCustomer.objects.none()
    contact_qs = CustomerContact.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    ).order_by("-is_primary", "contact_id")
    interaction_qs = Interaction.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    ).order_by("-interaction_at", "-interaction_id")
    return customer_information_queryset(
        TenantCustomer.objects.for_tenant(tenant)
        .filter(customer_status="LEAD")
        .select_related("industrial_unit")
        .annotate(
            contact_person_display=Subquery(contact_qs.values("contact_person")[:1]),
            mobile_display=Subquery(contact_qs.values("mobile_number")[:1]),
            phone_display=Subquery(contact_qs.values("phone_number")[:1]),
            has_contact=Exists(contact_qs),
            last_interaction_at=Subquery(interaction_qs.values("interaction_at")[:1]),
            has_interaction=Exists(interaction_qs),
        )
    )


def filter_prospective_customers(queryset, *, query="", contact_state="", quality="", ordering="newest"):
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(industrial_unit__official_name__icontains=query)
            | Q(industrial_unit__national_id__icontains=query)
            | Q(contacts__contact_person__icontains=query)
            | Q(contacts__mobile_number__icontains=query)
            | Q(contacts__phone_number__icontains=query)
            | Q(contacts__email__icontains=query)
        ).distinct()
    if contact_state == "with":
        queryset = queryset.filter(has_contact=True)
    elif contact_state == "without":
        queryset = queryset.filter(has_contact=False)
    queryset = filter_information(customer_information_queryset(queryset), quality)
    if ordering == "name":
        return queryset.order_by("industrial_unit__official_name", "tenant_customer_id")
    if ordering == "oldest":
        return queryset.order_by("created_at", "tenant_customer_id")
    return queryset.order_by("-created_at", "-tenant_customer_id")


def get_prospective_customer(tenant, customer_id):
    return get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
        customer_status="LEAD",
    )


@transaction.atomic
def create_prospective_customer(*, tenant, cleaned_data):
    normalized = normalize_company_name(cleaned_data["official_name"])
    # A01: a normalized name is a collision signal, never proof of identity.
    # Do not reveal existing records or create memberships across companies.
    if IndustrialUnit.objects.filter(normalized_name=normalized).exists():
        raise ValidationError(
            "ثبت انجام نشد؛ نام مشابهی در اطلاعات موجود است. "
            "تطبیق هویت و تأیید مسئول سامانه لازم است؛ اتصال خودکار انجام نشد.",
            code="identity_review_required",
        )
    unit = IndustrialUnit.objects.create(
        official_name=cleaned_data["official_name"].strip(),
        normalized_name=normalized,
        national_id=cleaned_data.get("national_id") or None,
        economic_code=cleaned_data.get("economic_code") or None,
        postal_code=cleaned_data.get("postal_code") or None,
        address=cleaned_data.get("address") or None,
        ownership_type="UNKNOWN",
        data_quality_status="REVIEW_REQUIRED",
    )
    customer = TenantCustomer.objects.create(
        tenant_id=tenant.tenant_id,
        industrial_unit=unit,
        customer_status="LEAD",
        data_quality_status="REVIEW_REQUIRED",
    )
    contact_values = {
        name: cleaned_data.get(name) or None
        for name in ("contact_person", "mobile_number", "phone_number", "email", "website")
    }
    CustomerContact.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer=customer,
        is_primary=True,
        data_quality_status="REVIEW_REQUIRED",
        **contact_values,
    )
    return customer


@transaction.atomic
def update_prospective_customer(*, tenant, customer, cleaned_data):
    customer = get_prospective_customer(tenant, customer.tenant_customer_id)
    unit = customer.industrial_unit
    normalized = normalize_company_name(cleaned_data["official_name"])
    duplicate = (
        TenantCustomer.objects.for_tenant(tenant)
        .filter(industrial_unit__normalized_name=normalized)
        .exclude(tenant_customer_id=customer.tenant_customer_id)
        .exists()
    )
    if duplicate:
        raise ValidationError("مشترک بالقوه دیگری با این نام ثبت شده است.")
    shared = TenantCustomer.objects.filter(industrial_unit_id=unit.industrial_unit_id).exclude(
        tenant_customer_id=customer.tenant_customer_id
    ).exists()
    values = {
        "official_name": cleaned_data["official_name"].strip(),
        "normalized_name": normalized,
        "national_id": cleaned_data.get("national_id") or None,
        "economic_code": cleaned_data.get("economic_code") or None,
        "postal_code": cleaned_data.get("postal_code") or None,
        "address": cleaned_data.get("address") or None,
    }
    if shared:
        old_unit_id = unit.industrial_unit_id
        unit = IndustrialUnit.objects.create(
            **values, ownership_type=unit.ownership_type,
            data_quality_status="REVIEW_REQUIRED",
        )
        from .ownership import preserve_on_identity_copy
        preserve_on_identity_copy(tenant, old_unit_id, unit.industrial_unit_id)
        customer.industrial_unit = unit
        customer.data_quality_status = "REVIEW_REQUIRED"
        customer.save(update_fields=["industrial_unit", "data_quality_status", "updated_at"])
    else:
        for name, value in values.items():
            setattr(unit, name, value)
        unit.data_quality_status = "REVIEW_REQUIRED"
        unit.save()
    return customer


@transaction.atomic
def add_customer_contact(*, tenant, customer, cleaned_data):
    customer = get_prospective_customer(tenant, customer.tenant_customer_id)
    existing = CustomerContact.objects.for_tenant(tenant).filter(tenant_customer=customer)
    is_primary = bool(cleaned_data.get("is_primary")) or not existing.exists()
    if is_primary:
        existing.update(is_primary=False)
    return CustomerContact.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer=customer,
        contact_person=cleaned_data.get("contact_person") or None,
        mobile_number=cleaned_data.get("mobile_number") or None,
        phone_number=cleaned_data.get("phone_number") or None,
        email=cleaned_data.get("email") or None,
        website=cleaned_data.get("website") or None,
        is_primary=is_primary,
        data_quality_status="REVIEW_REQUIRED",
    )


@transaction.atomic
def record_customer_interaction(*, tenant, customer, user, cleaned_data):
    customer = get_prospective_customer(tenant, customer.tenant_customer_id)
    interaction = Interaction.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_type=cleaned_data["interaction_type"],
        interaction_at=timezone.now(),
        operator_name_original=user.get_full_name() or user.username,
        interaction_status=cleaned_data["interaction_status"],
        notes=cleaned_data["notes"],
        source_system="WEB",
    )
    due_at = cleaned_data.get("follow_up_jalali")
    if due_at:
        FollowUp.objects.create(
            tenant_id=tenant.tenant_id,
            tenant_customer_id=customer.tenant_customer_id,
            interaction_id=interaction.interaction_id,
            due_at=due_at,
            follow_up_status="OPEN",
            reason=cleaned_data.get("follow_up_reason") or cleaned_data["notes"],
        )
    return interaction


@transaction.atomic
def convert_to_active_customer(*, tenant, customer, user=None):
    # Lock the membership, not the industrial identity shared by other tenants.
    customer = get_object_or_404(
        TenantCustomer.objects.select_for_update().for_tenant(tenant),
        tenant_customer_id=customer.tenant_customer_id,
        customer_status="LEAD",
    )
    customer.customer_status = "ACTIVE"
    customer.updated_at = timezone.now()
    customer.save(update_fields=["customer_status", "updated_at"])
    _record_customer_status_history(
        tenant=tenant, customer=customer, previous_status="LEAD", new_status="ACTIVE",
        reason_code="LEAD_CONVERTED", reason_note="", user=user,
    )
    return customer


def interaction_rows_for_customer(tenant, customer):
    rows = Interaction.objects.for_tenant(tenant).filter(
        tenant_customer_id=customer.tenant_customer_id
    ).order_by("-interaction_at", "-interaction_id")[:50]
    return [
        {
            "item": item,
            "type_label": TYPE_LABELS.get(item.interaction_type, item.interaction_type),
            "status_label": STATUS_LABELS.get(item.interaction_status, item.interaction_status or "ثبت نشده"),
        }
        for item in rows
    ]


def active_customers_for_tenant(tenant):
    if tenant is None:
        return TenantCustomer.objects.none()
    contact_qs = CustomerContact.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    ).order_by("-is_primary", "contact_id")
    sale_contracts = Contract.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
        contract_direction="SALE",
    )
    contract_counts = (
        Contract.objects.filter(
            tenant_id=tenant.tenant_id,
            tenant_customer_id=OuterRef("tenant_customer_id"),
            contract_direction="SALE",
        )
        .values("tenant_customer_id")
        .annotate(total=Count("contract_id"))
        .values("total")
    )
    known_volume_lines = ContractLine.objects.filter(
        tenant_id=tenant.tenant_id,
        contract__tenant_customer_id=OuterRef("tenant_customer_id"),
        contract__contract_direction="SALE",
        contracted_volume__isnull=False,
    )
    return customer_information_queryset(
        TenantCustomer.objects.for_tenant(tenant)
        .filter(customer_status="ACTIVE")
        .select_related("industrial_unit")
        .annotate(
            contact_person_display=Subquery(contact_qs.values("contact_person")[:1]),
            mobile_display=Subquery(contact_qs.values("mobile_number")[:1]),
            phone_display=Subquery(contact_qs.values("phone_number")[:1]),
            has_contact=Exists(contact_qs),
            has_contract=Exists(sale_contracts),
            has_known_volume=Exists(known_volume_lines),
            contract_count=Coalesce(
                Subquery(contract_counts[:1], output_field=IntegerField()),
                Value(0),
            ),
        )
    )


def filter_active_customers(
    queryset, *, query="", contract_data="", quality="", ordering="name"
):
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(industrial_unit__official_name__icontains=query)
            | Q(industrial_unit__national_id__icontains=query)
            | Q(contacts__contact_person__icontains=query)
            | Q(contacts__mobile_number__icontains=query)
            | Q(contacts__phone_number__icontains=query)
            | Q(contacts__email__icontains=query)
        ).distinct()
    if contract_data == "with_volume":
        queryset = queryset.filter(has_known_volume=True)
    elif contract_data == "without_volume":
        queryset = queryset.filter(has_known_volume=False)
    queryset = filter_information(customer_information_queryset(queryset), quality)
    if ordering == "contracts":
        return queryset.order_by(
            "-contract_count", "industrial_unit__official_name", "tenant_customer_id"
        )
    if ordering == "newest":
        return queryset.order_by("-created_at", "-tenant_customer_id")
    return queryset.order_by("industrial_unit__official_name", "tenant_customer_id")


def get_active_customer(tenant, customer_id):
    return get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
        customer_status="ACTIVE",
    )


def _jalali_date(value):
    if not value:
        return None
    return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")


def _first_value(*values):
    return next((value for value in values if value is not None), None)


def customer_contract_rows(tenant, customer, direction=None):
    monthly_qs = ContractMonthlyQuantity.objects.for_tenant(tenant).order_by(
        "period_jalali", "monthly_quantity_id"
    )
    line_qs = (
        ContractLine.objects.for_tenant(tenant)
        .select_related("energy_type", "load_band")
        .prefetch_related(
            Prefetch("monthly_quantities", queryset=monthly_qs)
        )
        .order_by("contract_line_id")
    )
    contract_filters = {"tenant_customer_id": customer.tenant_customer_id}
    if direction:
        contract_filters["contract_direction"] = direction
    contracts = (
        Contract.objects.for_tenant(tenant)
        .filter(**contract_filters)
        .prefetch_related(Prefetch("lines", queryset=line_qs))
        .order_by("-created_at", "-contract_id")
    )
    today = timezone.localdate()
    rows = []
    known_volume_total = Decimal("0")
    known_volume_lines = 0
    known_amount_lines = 0
    contracts_missing_dates = 0
    sale_contracts = 0
    purchase_contracts = 0

    for contract in contracts:
        if contract.contract_direction == "SALE":
            sale_contracts += 1
        elif contract.contract_direction == "PURCHASE":
            purchase_contracts += 1
        if contract.contract_start_gregorian and contract.contract_end_gregorian:
            if contract.contract_start_gregorian > today:
                lifecycle_label = "شروع‌نشده"
                lifecycle_class = "badge-warning"
            elif contract.contract_end_gregorian < today:
                lifecycle_label = "پایان‌یافته"
                lifecycle_class = "badge-muted"
            else:
                lifecycle_label = "فعال"
                lifecycle_class = "badge-success"
        else:
            lifecycle_label = "تاریخ نامشخص"
            lifecycle_class = "badge-warning"
            contracts_missing_dates += 1

        line_rows = []
        for line in contract.lines.all():
            rate_value = _first_value(
                line.corrected_rate,
                line.base_rate,
                line.adjusted_rate_1402,
                line.adjusted_rate_1401,
                line.rate_1401,
            )
            if line.contracted_volume is not None:
                known_volume_total += line.contracted_volume
                known_volume_lines += 1
            if line.amount_value is not None:
                known_amount_lines += 1
            line_rows.append(
                {
                    "item": line,
                    "rate_value": rate_value,
                    "monthly_quantities": list(line.monthly_quantities.all()),
                }
            )

        rows.append(
            {
                "item": contract,
                "display_number": (
                    contract.contract_number
                    or contract.external_reference
                    or f"قرارداد {contract.contract_id}"
                ),
                "start_display": (
                    contract.contract_start_jalali
                    or _jalali_date(contract.contract_start_gregorian)
                ),
                "end_display": (
                    contract.contract_end_jalali
                    or _jalali_date(contract.contract_end_gregorian)
                ),
                "lifecycle_label": lifecycle_label,
                "lifecycle_class": lifecycle_class,
                "direction_label": (
                    "فروش" if contract.contract_direction == "SALE" else "خرید"
                ),
                "lines": line_rows,
                "information": assess_contract(contract, line_rows),
            }
        )

    return {
        "rows": rows,
        "contract_count": len(rows),
        "contracts_missing_dates": contracts_missing_dates,
        "known_volume_total": known_volume_total,
        "known_volume_lines": known_volume_lines,
        "known_amount_lines": known_amount_lines,
        "sale_contracts": sale_contracts,
        "purchase_contracts": purchase_contracts,
    }


def active_contract_rows(tenant, customer):
    return customer_contract_rows(tenant, customer, direction="SALE")


def dormant_customers_for_tenant(tenant):
    if tenant is None:
        return TenantCustomer.objects.none()
    contact_qs = CustomerContact.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    ).order_by("-is_primary", "contact_id")
    interaction_qs = Interaction.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    ).order_by("-interaction_at", "-interaction_id")
    dormant_history = CustomerStatusHistory.objects.filter(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
        new_status="DORMANT",
    ).order_by("-changed_at", "-history_id")
    return (
        TenantCustomer.objects.for_tenant(tenant)
        .filter(customer_status="DORMANT")
        .select_related("industrial_unit")
        .annotate(
            contact_person_display=Subquery(contact_qs.values("contact_person")[:1]),
            mobile_display=Subquery(contact_qs.values("mobile_number")[:1]),
            phone_display=Subquery(contact_qs.values("phone_number")[:1]),
            last_interaction_at=Subquery(interaction_qs.values("interaction_at")[:1]),
            dormant_reason_code=Subquery(dormant_history.values("reason_code")[:1]),
            dormant_reason_note=Subquery(dormant_history.values("reason_note")[:1]),
            dormant_since=Subquery(dormant_history.values("changed_at")[:1]),
            previous_customer_status=Subquery(
                dormant_history.values("previous_status")[:1]
            ),
        )
    )


def filter_dormant_customers(
    queryset, *, tenant, query="", reason_code="", previous_status="", ordering="latest"
):
    query = (query or "").strip()
    if query:
        matching_history = CustomerStatusHistory.objects.for_tenant(tenant).filter(
            Q(reason_note__icontains=query) | Q(changed_by_name__icontains=query)
        ).values("tenant_customer_id")
        queryset = queryset.filter(
            Q(industrial_unit__official_name__icontains=query)
            | Q(industrial_unit__national_id__icontains=query)
            | Q(contacts__contact_person__icontains=query)
            | Q(contacts__mobile_number__icontains=query)
            | Q(contacts__phone_number__icontains=query)
            | Q(tenant_customer_id__in=matching_history)
        ).distinct()
    if reason_code:
        queryset = queryset.filter(dormant_reason_code=reason_code)
    if previous_status in {"ACTIVE", "LEAD"}:
        queryset = queryset.filter(previous_customer_status=previous_status)
    if ordering == "name":
        return queryset.order_by("industrial_unit__official_name", "tenant_customer_id")
    if ordering == "oldest":
        return queryset.order_by("dormant_since", "tenant_customer_id")
    return queryset.order_by("-dormant_since", "-tenant_customer_id")


def get_dormant_customer(tenant, customer_id):
    return get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
        customer_status="DORMANT",
    )


def get_status_change_candidate(tenant, customer_id):
    return get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
        customer_status__in=("LEAD", "ACTIVE"),
    )


def _user_audit_values(user):
    user_id = getattr(user, "pk", None)
    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    username = getattr(user, "username", "")
    return user_id, full_name or username or "کاربر سامانه"


def _record_customer_status_history(
    *, tenant, customer, previous_status, new_status, reason_code, reason_note, user
):
    # All callers hold the membership row lock inside their business transaction.
    user_id, user_name = _user_audit_values(user)
    history = CustomerStatusHistory.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        previous_status=previous_status,
        new_status=new_status,
        reason_code=reason_code,
        reason_note=(reason_note or "").strip() or None,
        changed_by_user_id=user_id,
        changed_by_name=user_name,
    )
    event = record_audit_event(
        action="STATUS_CHANGE", user=user, tenant=tenant,
        entity_type="CUSTOMER", entity_id=customer.tenant_customer_id,
        description="تغییر وضعیت مشتری همراه با سابقه ثبت شد.", severity="IMPORTANT",
        metadata={
            "operation": "customer_status_change", "history_id": history.pk,
            "previous_status": previous_status, "new_status": new_status,
            "reason_code": reason_code,
        },
    )
    if event is None:
        raise ValidationError("ثبت سابقهٔ ممیزی ممکن نشد؛ تغییر وضعیت ذخیره نشد. خطا را گزارش دهید.")
    return history


@transaction.atomic
def mark_customer_dormant(*, tenant, customer_id, user, reason_code, reason_note=""):
    if reason_code not in DORMANT_REASON_LABELS or reason_code == "REACTIVATED":
        raise ValidationError("علت توقف همکاری معتبر نیست.")
    customer = get_object_or_404(
        TenantCustomer.objects.select_for_update().for_tenant(tenant).select_related(
            "industrial_unit"
        ),
        tenant_customer_id=customer_id,
        customer_status__in=("LEAD", "ACTIVE"),
    )
    previous_status = customer.customer_status
    customer.customer_status = "DORMANT"
    customer.updated_at = timezone.now()
    customer.save(update_fields=["customer_status", "updated_at"])
    _record_customer_status_history(
        tenant=tenant, customer=customer, previous_status=previous_status, new_status="DORMANT",
        reason_code=reason_code, reason_note=reason_note, user=user,
    )
    return customer


@transaction.atomic
def restore_dormant_customer(*, tenant, customer_id, user, target_status, reason_note=""):
    if target_status not in {"LEAD", "ACTIVE"}:
        raise ValidationError("وضعیت مقصد معتبر نیست.")
    customer = get_object_or_404(
        TenantCustomer.objects.select_for_update().for_tenant(tenant).select_related(
            "industrial_unit"
        ),
        tenant_customer_id=customer_id,
        customer_status="DORMANT",
    )
    customer.customer_status = target_status
    customer.updated_at = timezone.now()
    customer.save(update_fields=["customer_status", "updated_at"])
    _record_customer_status_history(
        tenant=tenant, customer=customer, previous_status="DORMANT", new_status=target_status,
        reason_code="REACTIVATED", reason_note=reason_note, user=user,
    )
    return customer


def customer_status_history_rows(tenant, customer):
    rows = CustomerStatusHistory.objects.for_tenant(tenant).filter(
        tenant_customer_id=customer.tenant_customer_id
    )[:50]
    return [
        {
            "item": item,
            "previous_label": CUSTOMER_STATUS_LABELS.get(
                item.previous_status, item.previous_status
            ),
            "new_label": CUSTOMER_STATUS_LABELS.get(item.new_status, item.new_status),
            "reason_label": CUSTOMER_HISTORY_REASON_LABELS.get(
                item.reason_code, item.reason_code
            ),
        }
        for item in rows
    ]


def get_customer_for_profile(tenant, customer_id):
    return get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
    )


def build_customer_profile(tenant, customer):
    contacts = list(
        CustomerContact.objects.for_tenant(tenant).filter(tenant_customer=customer)
    )
    customer.information = assess_customer(customer, contacts)
    aliases = list(
        IndustrialUnitAlias.objects.filter(
            industrial_unit=customer.industrial_unit
        ).order_by("alias_name")
    )
    billing_accounts = list(
        BillingAccount.objects.for_tenant(tenant).filter(tenant_customer=customer)
    )
    interaction_qs = Interaction.objects.for_tenant(tenant).filter(
        tenant_customer_id=customer.tenant_customer_id
    )
    interactions = interaction_rows_for_customer(tenant, customer)
    follow_up_qs = FollowUp.objects.for_tenant(tenant).filter(
        tenant_customer_id=customer.tenant_customer_id
    )
    follow_ups = list(
        follow_up_qs.order_by(
            "follow_up_status", "due_at", "follow_up_id"
        )[:50]
    )
    status_history_qs = CustomerStatusHistory.objects.for_tenant(tenant).filter(
        tenant_customer_id=customer.tenant_customer_id
    )
    status_history = customer_status_history_rows(tenant, customer)
    contract_data = customer_contract_rows(tenant, customer)

    meters = list(
        Meter.objects.for_tenant(tenant)
        .filter(tenant_customer=customer)
    )
    meter_rows = []
    reading_count = 0
    latest_reading_at = None
    for meter in meters:
        meter_readings = MeterReading.objects.for_tenant(tenant).filter(meter=meter)
        reading_count += meter_readings.count()
        readings = list(
            meter_readings.order_by("-reading_at", "-meter_reading_id")[:20]
        )
        if readings and (
            latest_reading_at is None or readings[0].reading_at > latest_reading_at
        ):
            latest_reading_at = readings[0].reading_at
        meter_rows.append({"item": meter, "readings": readings[:20]})

    last_interaction_at = None
    if interactions:
        last_interaction_at = interactions[0]["item"].interaction_at

    return {
        "status_label": CUSTOMER_STATUS_LABELS.get(
            customer.customer_status, customer.customer_status
        ),
        "contacts": contacts,
        "aliases": aliases,
        "billing_accounts": billing_accounts,
        "interaction_rows": interactions,
        "follow_ups": follow_ups,
        "status_history_rows": status_history,
        "contract_data": contract_data,
        "meter_rows": meter_rows,
        "summary": {
            "contact_count": len(contacts),
            "billing_account_count": len(billing_accounts),
            "contract_count": contract_data["contract_count"],
            "interaction_count": interaction_qs.count(),
            "open_follow_up_count": follow_up_qs.filter(
                follow_up_status="OPEN"
            ).count(),
            "status_change_count": status_history_qs.count(),
            "meter_count": len(meters),
            "reading_count": reading_count,
            "last_interaction_at": last_interaction_at,
            "latest_reading_at": latest_reading_at,
        },
    }
