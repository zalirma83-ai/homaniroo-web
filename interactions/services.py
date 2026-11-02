from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from customers.ownership import attach_ownership, filter_related, normalize as normalize_ownership

from customers.models import CustomerContact, TenantCustomer
from .models import FollowUp, Interaction


TYPE_LABELS = {
    "CALL": "تماس تلفنی",
    "MEETING": "جلسه",
    "MESSAGE": "پیام‌رسان",
    "EMAIL": "ایمیل",
    "FOLLOW_UP": "ثبت اقدام پیگیری",
    "OTHER": "سایر",
}
RESULT_LABELS = {
    "INITIAL_CONTACT": "تماس اولیه انجام شد",
    "NO_ANSWER": "پاسخ نداد",
    "INTERESTED": "علاقه‌مند به ادامه مذاکره",
    "FOLLOW_UP_REQUIRED": "نیازمند پیگیری مجدد",
    "NOT_INTERESTED": "عدم تمایل",
    "WRONG_NUMBER": "شماره اشتباه",
    "CONTRACTED_ELSEWHERE": "دارای قرارداد با مجموعه دیگر",
    "COMPLETED": "پیگیری انجام شد",
    "CANCELLED": "پیگیری لغو شد",
    "RESCHEDULED": "زمان پیگیری تغییر کرد",
    "OTHER": "سایر",
}
MISSED_CALL_TERMS = (
    "NO_ANSWER",
    "جواب نداد",
    "پاسخ نداد",
    "عدم پاسخ",
    "در دسترس نبود",
    "در دستر نبود",
)


def _user_display(user):
    return user.get_full_name() or user.username


def _time_boundaries():
    today = timezone.localdate()
    current_tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    tomorrow_start = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=8)
    return today, today_start, tomorrow_start, week_end


def _missed_filter():
    query = Q()
    for term in MISSED_CALL_TERMS:
        query |= Q(interaction_status__icontains=term)
    return query


def get_customer(tenant, customer_id):
    customer = get_object_or_404(
        TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit"),
        tenant_customer_id=customer_id,
    )
    attach_ownership([customer], tenant)
    return customer


@transaction.atomic
def record_interaction(*, tenant, user, cleaned_data):
    customer = get_customer(tenant, cleaned_data["tenant_customer_id"])
    interaction = Interaction.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=customer.tenant_customer_id,
        interaction_type=cleaned_data["interaction_type"],
        interaction_at=timezone.now(),
        operator_name_original=_user_display(user),
        interaction_status=cleaned_data["interaction_status"],
        notes=(cleaned_data.get("notes") or "").strip() or None,
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
            reason=(cleaned_data.get("follow_up_reason") or "").strip()
            or (cleaned_data.get("notes") or "").strip()
            or "پیگیری تماس ثبت‌شده",
        )
    return interaction


def _customer_map(tenant, customer_ids):
    customers = (
        TenantCustomer.objects.for_tenant(tenant)
        .filter(tenant_customer_id__in=customer_ids)
        .select_related("industrial_unit")
    )
    return {item.tenant_customer_id: item for item in attach_ownership(customers, tenant)}


def _contact_map(tenant, customer_ids):
    contacts = (
        CustomerContact.objects.for_tenant(tenant)
        .filter(tenant_customer_id__in=customer_ids)
        .order_by("tenant_customer_id", "-is_primary", "contact_id")
    )
    result = {}
    for item in contacts:
        result.setdefault(item.tenant_customer_id, item)
    return result


def _scope_queryset(queryset, scope, today_start, tomorrow_start, week_end):
    if scope == "overdue":
        return queryset.filter(due_at__lt=today_start)
    if scope == "today":
        return queryset.filter(due_at__gte=today_start, due_at__lt=tomorrow_start)
    if scope == "upcoming":
        return queryset.filter(due_at__gte=tomorrow_start, due_at__lt=week_end)
    if scope == "without-date":
        return queryset.filter(due_at__isnull=True)
    return queryset


def build_work_queue(
    tenant, *, scope="all", query="", customer_status="", ordering="due",
    page_number=None, history_page_number=None, ownership="",
):
    _, today_start, tomorrow_start, week_end = _time_boundaries()
    ownership = normalize_ownership(ownership)
    open_qs = filter_related(FollowUp.objects.for_tenant(tenant).filter(follow_up_status="OPEN"), tenant, ownership)
    stats = open_qs.aggregate(
        open=Count("follow_up_id"),
        overdue=Count("follow_up_id", filter=Q(due_at__lt=today_start)),
        today=Count(
            "follow_up_id",
            filter=Q(due_at__gte=today_start, due_at__lt=tomorrow_start),
        ),
        upcoming=Count(
            "follow_up_id",
            filter=Q(due_at__gte=tomorrow_start, due_at__lt=week_end),
        ),
        without_date=Count("follow_up_id", filter=Q(due_at__isnull=True)),
    )
    valid_scopes = {"all", "all-records", "overdue", "today", "upcoming", "without-date"}
    scope = scope if scope in valid_scopes else "all"
    queryset = _scope_queryset(
        FollowUp.objects.for_tenant(tenant) if scope == "all-records" else open_qs,
        scope, today_start, tomorrow_start, week_end
    )

    queryset = filter_related(queryset, tenant, ownership)
    query = (query or "").strip()
    if query:
        matching_ids = (
            TenantCustomer.objects.for_tenant(tenant)
            .filter(
                Q(industrial_unit__official_name__icontains=query)
                | Q(contacts__contact_person__icontains=query)
                | Q(contacts__mobile_number__icontains=query)
                | Q(contacts__phone_number__icontains=query)
            )
            .values_list("tenant_customer_id", flat=True)
            .distinct()
        )
        queryset = queryset.filter(
            Q(tenant_customer_id__in=matching_ids) | Q(reason__icontains=query)
        )

    valid_customer_statuses = {"ACTIVE", "LEAD", "DORMANT"}
    customer_status = (
        customer_status if customer_status in valid_customer_statuses else ""
    )
    if customer_status:
        status_customer_ids = TenantCustomer.objects.for_tenant(tenant).filter(
            customer_status=customer_status
        ).values_list("tenant_customer_id", flat=True)
        queryset = queryset.filter(tenant_customer_id__in=status_customer_ids)

    valid_orderings = {"due", "due-desc", "newest"}
    ordering = ordering if ordering in valid_orderings else "due"
    if ordering == "due-desc":
        order_fields = (F("due_at").desc(nulls_last=True), "-follow_up_id")
    elif ordering == "newest":
        order_fields = ("-follow_up_id",)
    else:
        order_fields = (F("due_at").asc(nulls_last=True), "follow_up_id")
    followup_page = Paginator(queryset.order_by(*order_fields), 25).get_page(page_number)
    follow_ups = list(followup_page.object_list)
    customer_ids = {item.tenant_customer_id for item in follow_ups}
    customers = _customer_map(tenant, customer_ids)
    contacts = _contact_map(tenant, customer_ids)
    rows = []
    now = timezone.now()
    current_tz = timezone.get_current_timezone()
    for item in follow_ups:
        comparable_due = item.due_at
        if comparable_due is not None and timezone.is_naive(comparable_due):
            comparable_due = timezone.make_aware(comparable_due, current_tz)
        can_act = item.follow_up_status == "OPEN"
        if item.follow_up_status == "COMPLETED":
            due_label, due_tone = "انجام‌شده", "success"
        elif item.follow_up_status == "CANCELLED":
            due_label, due_tone = "لغوشده", "muted"
        elif not can_act:
            due_label, due_tone = "وضعیت نامشخص", "muted"
        elif comparable_due is None:
            due_label, due_tone = "بدون تاریخ", "muted"
        elif comparable_due < today_start:
            due_label, due_tone = "عقب‌افتاده", "danger"
        elif comparable_due < tomorrow_start:
            due_label, due_tone = "امروز", "warning"
        else:
            due_label, due_tone = "آینده", "info"
        rows.append(
            {
                "item": item,
                "customer": customers.get(item.tenant_customer_id),
                "contact": contacts.get(item.tenant_customer_id),
                "due_label": due_label,
                "due_tone": due_tone,
                "can_act": can_act,
                "is_due": bool(can_act and comparable_due and comparable_due <= now),
            }
        )

    history_page = Paginator(
        filter_related(Interaction.objects.for_tenant(tenant), tenant, ownership).order_by(
            "-interaction_at", "-interaction_id"
        ),
        30,
    ).get_page(history_page_number)
    recent = list(history_page.object_list)
    recent_customer_ids = {item.tenant_customer_id for item in recent}
    recent_customers = _customer_map(tenant, recent_customer_ids)
    recent_rows = [
        {
            "item": item,
            "customer": recent_customers.get(item.tenant_customer_id),
            "type_label": TYPE_LABELS.get(item.interaction_type, item.interaction_type),
            "result_label": RESULT_LABELS.get(
                item.interaction_status,
                item.interaction_status or "نتیجه ثبت نشده",
            ),
        }
        for item in recent
    ]
    interaction_stats = filter_related(Interaction.objects.for_tenant(tenant), tenant, ownership).aggregate(
        total=Count("interaction_id"),
        missed=Count("interaction_id", filter=_missed_filter()),
    )
    return {
        "stats": stats,
        "rows": rows,
        "recent_rows": recent_rows,
        "followup_page": followup_page,
        "history_page": history_page,
        "interaction_stats": interaction_stats,
        "scope": scope,
        "query": query,
        "customer_status": customer_status,
        "ordering": ordering,
        "ownership_filter": ownership,
    }


def get_open_follow_up(tenant, follow_up_id, *, lock=False):
    queryset = FollowUp.objects.for_tenant(tenant)
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(
        queryset,
        follow_up_id=follow_up_id,
        follow_up_status="OPEN",
    )


def _log_follow_up_action(*, tenant, follow_up, user, status, note):
    return Interaction.objects.create(
        tenant_id=tenant.tenant_id,
        tenant_customer_id=follow_up.tenant_customer_id,
        interaction_type="FOLLOW_UP",
        interaction_at=timezone.now(),
        operator_name_original=_user_display(user),
        interaction_status=status,
        notes=(note or "").strip() or None,
        source_system="WEB",
        source_record_id=f"follow_up:{follow_up.follow_up_id}",
    )


@transaction.atomic
def close_follow_up(*, tenant, follow_up_id, user, action, note=""):
    if action not in {"DONE", "CANCELLED"}:
        raise ValidationError("عملیات پیگیری معتبر نیست.")
    follow_up = get_open_follow_up(tenant, follow_up_id, lock=True)
    follow_up.follow_up_status = "COMPLETED" if action == "DONE" else "CANCELLED"
    follow_up.save(update_fields=["follow_up_status"])
    _log_follow_up_action(
        tenant=tenant,
        follow_up=follow_up,
        user=user,
        status="COMPLETED" if action == "DONE" else "CANCELLED",
        note=note or ("پیگیری انجام شد." if action == "DONE" else "پیگیری لغو شد."),
    )
    return follow_up


@transaction.atomic
def reschedule_follow_up(*, tenant, follow_up_id, user, due_at, reason=""):
    follow_up = get_open_follow_up(tenant, follow_up_id, lock=True)
    follow_up.due_at = due_at
    if (reason or "").strip():
        follow_up.reason = reason.strip()
    follow_up.save(update_fields=["due_at", "reason"])
    _log_follow_up_action(
        tenant=tenant,
        follow_up=follow_up,
        user=user,
        status="RESCHEDULED",
        note=f"زمان جدید پیگیری ثبت شد. {reason or ''}".strip(),
    )
    return follow_up
