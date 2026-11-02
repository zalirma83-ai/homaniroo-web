from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from customers.ownership import attach_ownership, filter_related, normalize as normalize_ownership

from contracts.models import Contract
from customers.models import TenantCustomer
from .models import Alert, FollowUp, Interaction
from .scope_locks import lock_alert_scope


MANAGED_ALERT_TYPES = {
    "FOLLOW_UP",
    "CONTRACT_ENDING",
    "CONTRACT_EXPIRED",
    "CUSTOMER_NO_CONTACT",
    "MISSED_CALL",
}
TYPE_LABELS = {
    "FOLLOW_UP": "پیگیری مشتری",
    "CONTRACT_ENDING": "پایان نزدیک قرارداد",
    "CONTRACT_EXPIRED": "قرارداد منقضی‌شده",
    "CUSTOMER_NO_CONTACT": "مشتری بدون تماس",
    "MISSED_CALL": "تماس بی‌پاسخ",
}
STATUS_LABELS = {
    "OPEN": "باز",
    "SEEN": "مشاهده‌شده",
    "COMPLETED": "رسیدگی‌شده",
    "DISMISSED": "کنار گذاشته‌شده",
}
SEVERITY_LABELS = {
    "CRITICAL": "فوری",
    "IMPORTANT": "مهم",
    "NORMAL": "عادی",
}
SEVERITY_CLASSES = {
    "CRITICAL": "danger",
    "IMPORTANT": "warning",
    "NORMAL": "info",
}
MISSED_CALL_TERMS = (
    "NO_ANSWER",
    "جواب نداد",
    "پاسخ نداد",
    "عدم پاسخ",
    "در دسترس نبود",
    "در دستر نبود",
)
ACTIVE_ALERT_STATUSES = ("OPEN", "SEEN")


def _tenant_id(tenant):
    return getattr(tenant, "tenant_id", tenant)


def _aware_midnight(value):
    if value is None:
        return None
    current_tz = timezone.get_current_timezone()
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, current_tz)
        return value
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time.min), current_tz)
    return value


def _is_missed(status):
    normalized = (status or "").casefold()
    return any(term.casefold() in normalized for term in MISSED_CALL_TERMS)


def _alert_key(alert_type, *, customer_id=None, follow_up_id=None, contract_id=None):
    if alert_type == "FOLLOW_UP":
        return alert_type, follow_up_id
    if alert_type in {"CONTRACT_ENDING", "CONTRACT_EXPIRED"}:
        return alert_type, contract_id
    return alert_type, customer_id


def _desired_alerts(tenant):
    tenant_id = _tenant_id(tenant)
    today = timezone.localdate()
    next_week = today + timedelta(days=7)
    next_month = today + timedelta(days=30)
    desired = {}

    follow_ups = FollowUp.objects.for_tenant(tenant).filter(follow_up_status="OPEN")
    for item in follow_ups:
        due_at = _aware_midnight(item.due_at)
        due_date = timezone.localtime(due_at).date() if due_at else None
        if due_date is None:
            severity = "NORMAL"
            message = "برای این پیگیری هنوز تاریخ انجام تعیین نشده است."
        elif due_date < today:
            severity = "CRITICAL"
            message = f"پیگیری از تاریخ {due_date.isoformat()} عقب افتاده است."
        elif due_date <= next_week:
            severity = "IMPORTANT"
            message = f"زمان این پیگیری تا {due_date.isoformat()} فرامی‌رسد."
        else:
            continue
        key = _alert_key("FOLLOW_UP", follow_up_id=item.follow_up_id)
        desired[key] = {
            "tenant_id": tenant_id,
            "tenant_customer_id": item.tenant_customer_id,
            "follow_up_id": item.follow_up_id,
            "contract_id": None,
            "alert_type": "FOLLOW_UP",
            "alert_at": due_at,
            "severity": severity,
            "message": message,
        }

    contracts = Contract.objects.for_tenant(tenant).filter(
        contract_end_gregorian__isnull=False
    )
    for item in contracts:
        end_date = item.contract_end_gregorian
        display_number = item.contract_number or f"شناسه {item.contract_id}"
        if end_date < today:
            alert_type = "CONTRACT_EXPIRED"
            severity = "CRITICAL"
            message = f"قرارداد {display_number} در تاریخ {end_date.isoformat()} منقضی شده است."
        elif end_date <= next_month:
            alert_type = "CONTRACT_ENDING"
            severity = "IMPORTANT"
            message = f"قرارداد {display_number} تا تاریخ {end_date.isoformat()} پایان می‌یابد."
        else:
            continue
        key = _alert_key(alert_type, contract_id=item.contract_id)
        desired[key] = {
            "tenant_id": tenant_id,
            "tenant_customer_id": item.tenant_customer_id,
            "follow_up_id": None,
            "contract_id": item.contract_id,
            "alert_type": alert_type,
            "alert_at": _aware_midnight(end_date),
            "severity": severity,
            "message": message,
        }

    interaction_exists = Interaction.objects.filter(
        tenant_id=tenant_id,
        tenant_customer_id=OuterRef("tenant_customer_id"),
    )
    never_contacted = (
        TenantCustomer.objects.for_tenant(tenant)
        .annotate(has_interaction=Exists(interaction_exists))
        .filter(has_interaction=False)
        .select_related("industrial_unit")
    )
    for customer in never_contacted:
        key = _alert_key(
            "CUSTOMER_NO_CONTACT", customer_id=customer.tenant_customer_id
        )
        desired[key] = {
            "tenant_id": tenant_id,
            "tenant_customer_id": customer.tenant_customer_id,
            "follow_up_id": None,
            "contract_id": None,
            "alert_type": "CUSTOMER_NO_CONTACT",
            "alert_at": None,
            "severity": "NORMAL",
            "message": f"برای «{customer.industrial_unit.official_name}» هنوز هیچ تماسی ثبت نشده است.",
        }

    latest_by_customer = {}
    recent_interactions = Interaction.objects.for_tenant(tenant).order_by(
        "tenant_customer_id", "-interaction_at", "-interaction_id"
    )
    for item in recent_interactions:
        latest_by_customer.setdefault(item.tenant_customer_id, item)
    for customer_id, item in latest_by_customer.items():
        if not _is_missed(item.interaction_status):
            continue
        key = _alert_key("MISSED_CALL", customer_id=customer_id)
        desired[key] = {
            "tenant_id": tenant_id,
            "tenant_customer_id": customer_id,
            "follow_up_id": None,
            "contract_id": None,
            "alert_type": "MISSED_CALL",
            "alert_at": _aware_midnight(item.interaction_at),
            "severity": "IMPORTANT",
            "message": "آخرین تماس مشتری بی‌پاسخ بوده و نیازمند بررسی است.",
        }
    return desired


def _existing_key(item):
    return _alert_key(
        item.alert_type,
        customer_id=item.tenant_customer_id,
        follow_up_id=item.follow_up_id,
        contract_id=item.contract_id,
    )


@transaction.atomic
def synchronize_alerts(tenant):
    lock_alert_scope(tenant)
    desired = _desired_alerts(tenant)
    existing = list(
        Alert.objects.for_tenant(tenant).filter(alert_type__in=MANAGED_ALERT_TYPES)
    )
    existing_map = {}
    duplicates = []
    for item in existing:
        key = _existing_key(item)
        if key in existing_map:
            duplicates.append(item)
        else:
            existing_map[key] = item

    to_create = []
    to_update = []
    created = updated = resolved = 0
    update_fields = [
        "tenant_customer_id",
        "follow_up_id",
        "contract_id",
        "alert_at",
        "severity",
        "alert_status",
        "message",
    ]
    for key, values in desired.items():
        item = existing_map.pop(key, None)
        if item is None:
            to_create.append(Alert(alert_status="OPEN", **values))
            created += 1
            continue
        changed = False
        for field, value in values.items():
            if field == "tenant_id":
                continue
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if item.alert_status == "COMPLETED":
            item.alert_status = "OPEN"
            changed = True
        if changed:
            to_update.append(item)
            updated += 1

    stale = list(existing_map.values()) + duplicates
    for item in stale:
        if item.alert_status in ACTIVE_ALERT_STATUSES:
            item.alert_status = "COMPLETED"
            to_update.append(item)
            resolved += 1

    if to_create:
        Alert.objects.bulk_create(to_create)
    if to_update:
        unique_updates = {item.alert_id: item for item in to_update}
        Alert.objects.bulk_update(list(unique_updates.values()), update_fields)
    return {
        "created": created,
        "updated": updated,
        "resolved": resolved,
        "active": len(desired),
    }


def alert_quality_summary(tenant):
    return {
        "contracts_missing_end_date": Contract.objects.for_tenant(tenant).filter(
            contract_end_gregorian__isnull=True
        ).count(),
        "follow_ups_missing_date": FollowUp.objects.for_tenant(tenant).filter(
            follow_up_status="OPEN", due_at__isnull=True
        ).count(),
    }


def build_alert_center(
    tenant, *, status="active", alert_group="", severity="", query="", ownership=""
):
    ownership = normalize_ownership(ownership)
    base = filter_related(Alert.objects.for_tenant(tenant), tenant, ownership)
    stats = base.aggregate(
        total=Count("alert_id"),
        open=Count("alert_id", filter=Q(alert_status="OPEN")),
        acknowledged=Count(
            "alert_id", filter=Q(alert_status="SEEN")
        ),
        critical=Count(
            "alert_id",
            filter=Q(
                alert_status__in=ACTIVE_ALERT_STATUSES,
                severity="CRITICAL",
            ),
        ),
        warning=Count(
            "alert_id",
            filter=Q(
                alert_status__in=ACTIVE_ALERT_STATUSES,
                severity="IMPORTANT",
            ),
        ),
    )
    queryset = base
    valid_statuses = {"active", "OPEN", "SEEN", "COMPLETED", "DISMISSED", "all"}
    status = status if status in valid_statuses else "active"
    if status == "active":
        queryset = queryset.filter(alert_status__in=ACTIVE_ALERT_STATUSES)
    elif status != "all":
        queryset = queryset.filter(alert_status=status)

    groups = {
        "follow-up": {"FOLLOW_UP"},
        "contract": {"CONTRACT_ENDING", "CONTRACT_EXPIRED"},
        "customer": {"CUSTOMER_NO_CONTACT"},
        "missed-call": {"MISSED_CALL"},
    }
    if alert_group in groups:
        queryset = queryset.filter(alert_type__in=groups[alert_group])
    else:
        alert_group = ""
    if severity in SEVERITY_LABELS:
        queryset = queryset.filter(severity=severity)
    else:
        severity = ""

    query = (query or "").strip()
    if query:
        matching_customers = (
            TenantCustomer.objects.for_tenant(tenant)
            .filter(industrial_unit__official_name__icontains=query)
            .values_list("tenant_customer_id", flat=True)
        )
        queryset = queryset.filter(
            Q(message__icontains=query)
            | Q(tenant_customer_id__in=matching_customers)
        )

    alerts = list(
        queryset.order_by("alert_status", "-severity", "alert_at", "alert_id")
    )
    customer_ids = {
        item.tenant_customer_id for item in alerts if item.tenant_customer_id
    }
    customers = {
        item.tenant_customer_id: item
        for item in TenantCustomer.objects.for_tenant(tenant)
        .filter(tenant_customer_id__in=customer_ids)
        .select_related("industrial_unit")
    }
    attach_ownership(customers.values(), tenant)
    rows = [
        {
            "item": item,
            "customer": customers.get(item.tenant_customer_id),
            "type_label": TYPE_LABELS.get(item.alert_type, item.alert_type),
            "status_label": STATUS_LABELS.get(item.alert_status, item.alert_status),
            "severity_label": SEVERITY_LABELS.get(item.severity, item.severity),
            "severity_class": SEVERITY_CLASSES.get(item.severity, "muted"),
        }
        for item in alerts
    ]
    return {
        "stats": stats,
        "rows": rows,
        "quality": alert_quality_summary(tenant),
        "filters": {
            "status": status,
            "alert_group": alert_group,
            "severity": severity,
            "query": query,
            "ownership": ownership,
        },
    }


@transaction.atomic
def update_alert_status(*, tenant, alert_id, status):
    if status not in {"OPEN", "SEEN", "COMPLETED", "DISMISSED"}:
        raise ValueError("وضعیت هشدار معتبر نیست.")
    lock_alert_scope(tenant)
    alert = get_object_or_404(
        Alert.objects.for_tenant(tenant).select_for_update(), alert_id=alert_id
    )
    alert.alert_status = status
    alert.save(update_fields=["alert_status"])
    return alert
