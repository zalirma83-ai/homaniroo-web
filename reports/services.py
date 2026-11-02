from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Prefetch, Q, Sum
from django.utils import timezone

from common.date_filters import parse_filter_date
from common.information_status import (
    assess_contract, assess_customer, contract_information_queryset, customer_information_queryset,
)
from contracts.models import Contract, ContractLine, ContractMonthlyQuantity
from customers.models import CustomerContact, TenantCustomer
from customers.ownership import filter_customers, filter_related, normalize as normalize_ownership, LABELS
from energy.models import EnergyBalance, EnergyType, LoadBand
from interactions.models import Alert, FollowUp, Interaction


MISSED_CALL_TERMS = (
    "NO_ANSWER", "جواب نداد", "پاسخ نداد", "عدم پاسخ", "در دسترس نبود", "در دستر نبود",
)
EXPORT_KEYS = ("customers", "contracts", "interactions", "followups", "alerts", "energy")


def tenant_id(tenant):
    return getattr(tenant, "tenant_id", tenant)


def parse_report_dates(date_from="", date_to=""):
    def parse(value):
        try:
            return parse_filter_date(value)
        except (TypeError, ValueError):
            return None

    start_date, end_date = parse(date_from), parse(date_to)
    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), current_tz) if start_date else None
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), current_tz) if end_date else None
    return start_date, end_date, start, end


def filter_period(queryset, field, start=None, end=None):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__lt": end})
    return queryset


def _missed_filter():
    query = Q()
    for term in MISSED_CALL_TERMS:
        query |= Q(interaction_status__icontains=term)
    return query


def _latest_balances(tenant):
    queryset = EnergyBalance.objects.for_tenant(tenant)
    latest_run = queryset.aggregate(value=Max("validation_run_id"))["value"]
    if latest_run is None:
        return queryset.none(), None
    return queryset.filter(validation_run_id=latest_run), latest_run


def _customer_metrics(tenant, ownership=""):
    customers = customer_information_queryset(TenantCustomer.objects.for_tenant(tenant))
    customers = filter_customers(customers, tenant, ownership)
    customer_ids = customers.values_list("tenant_customer_id", flat=True)
    contacts = CustomerContact.objects.for_tenant(tenant).filter(tenant_customer_id__in=customer_ids)
    contacted_ids = Interaction.objects.for_tenant(tenant).values_list("tenant_customer_id", flat=True).distinct()
    with_contact = customers.filter(info_has_channel=True).count()
    return {
        **customers.aggregate(
            total=Count("tenant_customer_id"),
            active=Count("tenant_customer_id", filter=Q(customer_status="ACTIVE")),
            leads=Count("tenant_customer_id", filter=Q(customer_status="LEAD")),
            dormant=Count("tenant_customer_id", filter=Q(customer_status="DORMANT")),
            clean=Count("tenant_customer_id", filter=Q(info_incomplete=False, info_review=False)),
            review=Count("tenant_customer_id", filter=Q(info_incomplete=True) | Q(info_review=True)),
        ),
        "with_contact": with_contact,
        "without_contact": max(0, customers.count() - with_contact),
        "contacted": customers.filter(tenant_customer_id__in=contacted_ids).count(),
        "contact_rows": contacts.count(),
    }


def _contract_metrics(tenant, ownership=""):
    contracts = contract_information_queryset(Contract.objects.for_tenant(tenant))
    contracts = filter_related(contracts, tenant, ownership)
    lines = ContractLine.objects.for_tenant(tenant).filter(contract__tenant_id=tenant_id(tenant))
    if normalize_ownership(ownership):
        lines = lines.filter(contract_id__in=contracts.values("contract_id"))
    return {
        **contracts.aggregate(
            total=Count("contract_id"),
            sales=Count("contract_id", filter=Q(contract_direction="SALE")),
            purchases=Count("contract_id", filter=Q(contract_direction="PURCHASE")),
            review=Count("contract_id", filter=Q(info_incomplete=True) | Q(info_review=True)),
            missing_dates=Count(
                "contract_id",
                filter=Q(contract_start_gregorian__isnull=True) | Q(contract_end_gregorian__isnull=True),
            ),
        ),
        **lines.aggregate(
            lines=Count("contract_line_id"),
            known_volume=Sum("contracted_volume"),
            missing_volume=Count("contract_line_id", filter=Q(contracted_volume__isnull=True)),
            missing_rate=Count("contract_line_id", filter=Q(base_rate__isnull=True)),
            missing_amount=Count("contract_line_id", filter=Q(amount_value__isnull=True)),
        ),
    }


def _activity_metrics(tenant, start, end, ownership=""):
    interactions = filter_period(Interaction.objects.for_tenant(tenant), "interaction_at", start, end)
    followups = filter_period(FollowUp.objects.for_tenant(tenant), "due_at", start, end)
    alerts = filter_period(Alert.objects.for_tenant(tenant), "alert_at", start, end)
    interactions = filter_related(interactions, tenant, ownership)
    followups = filter_related(followups, tenant, ownership)
    alerts = filter_related(alerts, tenant, ownership)
    now = timezone.now()
    return {
        "interactions": interactions.aggregate(
            total=Count("interaction_id"),
            customers=Count("tenant_customer_id", distinct=True),
            missed=Count("interaction_id", filter=_missed_filter()),
        ),
        "followups": followups.aggregate(
            total=Count("follow_up_id"),
            open=Count("follow_up_id", filter=Q(follow_up_status="OPEN")),
            completed=Count("follow_up_id", filter=Q(follow_up_status="COMPLETED")),
            overdue=Count("follow_up_id", filter=Q(follow_up_status="OPEN", due_at__lt=now)),
            missing_date=Count("follow_up_id", filter=Q(due_at__isnull=True)),
        ),
        "alerts": alerts.aggregate(
            total=Count("alert_id"),
            open=Count("alert_id", filter=Q(alert_status="OPEN")),
            seen=Count("alert_id", filter=Q(alert_status="SEEN")),
            completed=Count("alert_id", filter=Q(alert_status="COMPLETED")),
            critical=Count("alert_id", filter=Q(severity="CRITICAL")),
        ),
        "interaction_statuses": list(
            interactions.values("interaction_status")
            .annotate(total=Count("interaction_id"))
            .order_by("-total", "interaction_status")[:8]
        ),
    }


def _energy_metrics(tenant):
    latest, run_id = _latest_balances(tenant)
    totals = latest.filter(balance_scope="CONTRACT_TOTAL").aggregate(
        rows=Count("energy_balance_id"),
        purchase=Sum("purchase_volume"),
        sales=Sum("sales_volume"),
        balance=Sum("balance_value"),
        deficit=Count("energy_balance_id", filter=Q(balance_status="DEFICIT")),
        surplus=Count("energy_balance_id", filter=Q(balance_status="SURPLUS")),
        incomplete=Count("energy_balance_id", filter=~Q(data_completeness="COMPLETE")),
    )
    for key in ("purchase", "sales", "balance"):
        totals[key] = totals[key] or Decimal("0")
    monthly = list(
        latest.filter(balance_scope="MONTHLY_AVAILABLE")
        .values("period_jalali")
        .annotate(
            purchase=Sum("purchase_volume"),
            sales=Sum("sales_volume"),
            balance=Sum("balance_value"),
            incomplete=Count("energy_balance_id", filter=~Q(data_completeness="COMPLETE")),
        )
        .order_by("period_jalali")
    )
    return {"latest_run": run_id, "totals": totals, "monthly": monthly}


def build_management_report(tenant, *, date_from="", date_to="", ownership=""):
    ownership = normalize_ownership(ownership)
    from_date, to_date, start, end = parse_report_dates(date_from, date_to)
    customers = _customer_metrics(tenant, ownership)
    contracts = _contract_metrics(tenant, ownership)
    activity = _activity_metrics(tenant, start, end, ownership)
    energy = _energy_metrics(tenant)
    return {
        "customers": customers,
        "contracts": contracts,
        "activity": activity,
        "energy": energy,
        "filters": {
            "date_from": from_date.isoformat() if from_date else "",
            "date_to": to_date.isoformat() if to_date else "",
            "active": bool(from_date or to_date),
            "ownership": ownership,
        },
        "quality": {
            "customer_review": customers["review"],
            "contract_review": contracts["review"],
            "contracts_missing_dates": contracts["missing_dates"],
            "contract_lines_missing_volume": contracts["missing_volume"],
            "energy_incomplete": energy["totals"]["incomplete"],
        },
    }


def _customer_name_map(tenant, ids):
    return {
        item.tenant_customer_id: item.industrial_unit.official_name
        for item in TenantCustomer.objects.for_tenant(tenant)
        .filter(tenant_customer_id__in=ids)
        .select_related("industrial_unit")
    }


def _export_rows(tenant, key, *, date_from="", date_to="", ownership=""):
    if key not in EXPORT_KEYS:
        raise ValueError("Unsupported report key")
    _, _, start, end = parse_report_dates(date_from, date_to)

    if key == "customers":
        queryset = TenantCustomer.objects.for_tenant(tenant).select_related("industrial_unit").prefetch_related(
            Prefetch("contacts", queryset=CustomerContact.objects.for_tenant(tenant))
        )
        queryset = filter_customers(queryset, tenant, ownership)
        header = ["شناسه پرونده", "نام مشتری", "وضعیت", "وضعیت اطلاعات", "شخص رابط", "موبایل", "تلفن", "ایمیل", "موارد نیازمند رسیدگی"]
        rows = []
        for item in queryset.order_by("tenant_customer_id"):
            contacts = list(item.contacts.all())
            information = assess_customer(item, contacts)
            primary = next((c for c in contacts if c.is_primary), contacts[0] if contacts else None)
            rows.append([
                item.tenant_customer_id, item.industrial_unit.official_name,
                item.customer_status, information.label,
                primary.contact_person if primary else "",
                primary.mobile_number if primary else "",
                primary.phone_number if primary else "",
                primary.email if primary else "", information.summary,
            ])
        return header, rows

    if key == "contracts":
        lines_qs = ContractLine.objects.for_tenant(tenant).prefetch_related(
            Prefetch("monthly_quantities", queryset=ContractMonthlyQuantity.objects.for_tenant(tenant))
        )
        queryset = Contract.objects.for_tenant(tenant).prefetch_related(
            Prefetch("lines", queryset=lines_qs)
        ).order_by("contract_id")
        queryset = filter_related(queryset, tenant, ownership)
        customer_ids = [item.tenant_customer_id for item in queryset if item.tenant_customer_id]
        names = _customer_name_map(tenant, customer_ids)
        header = ["شناسه قرارداد", "شماره قرارداد", "جهت", "طرف قرارداد", "نوع معامله", "شروع", "پایان", "حجم ثبت‌شده", "واحد", "وضعیت اطلاعات", "موارد نیازمند رسیدگی"]
        rows = []
        for item in queryset:
            lines = list(item.lines.all())
            information = assess_contract(item, [{"item": line, "monthly_quantities": list(line.monthly_quantities.all())} for line in lines])
            known = sum((line.contracted_volume or Decimal("0") for line in lines), Decimal("0"))
            unit = next((line.volume_unit for line in lines if line.volume_unit), "")
            rows.append([
                item.contract_id, item.contract_number or "", item.contract_direction,
                names.get(item.tenant_customer_id, f"طرف معامله {item.counterparty_id}" if item.counterparty_id else ""),
                item.transaction_type, item.contract_start_jalali or "", item.contract_end_jalali or "",
                known, unit, information.label, information.summary,
            ])
        return header, rows

    if key == "interactions":
        queryset = filter_period(Interaction.objects.for_tenant(tenant), "interaction_at", start, end).order_by("interaction_at", "interaction_id")
        queryset = filter_related(queryset, tenant, ownership)
        names = _customer_name_map(tenant, queryset.values_list("tenant_customer_id", flat=True))
        header = ["شناسه", "مشتری", "نوع ارتباط", "زمان", "وضعیت", "اپراتور", "یادداشت"]
        return header, [[item.interaction_id, names.get(item.tenant_customer_id, item.tenant_customer_id), item.interaction_type, item.interaction_at, item.interaction_status or "", item.operator_name_original or "", item.notes or ""] for item in queryset]

    if key == "followups":
        queryset = filter_period(FollowUp.objects.for_tenant(tenant), "due_at", start, end).order_by("due_at", "follow_up_id")
        queryset = filter_related(queryset, tenant, ownership)
        names = _customer_name_map(tenant, queryset.values_list("tenant_customer_id", flat=True))
        header = ["شناسه", "مشتری", "سررسید", "وضعیت", "دلیل"]
        return header, [[item.follow_up_id, names.get(item.tenant_customer_id, item.tenant_customer_id), item.due_at, item.follow_up_status, item.reason or ""] for item in queryset]

    if key == "alerts":
        queryset = filter_period(Alert.objects.for_tenant(tenant), "alert_at", start, end).order_by("alert_at", "alert_id")
        queryset = filter_related(queryset, tenant, ownership)
        customer_ids = queryset.exclude(tenant_customer_id__isnull=True).values_list("tenant_customer_id", flat=True)
        names = _customer_name_map(tenant, customer_ids)
        header = ["شناسه", "مشتری", "نوع هشدار", "زمان", "اهمیت", "وضعیت", "شرح"]
        return header, [[item.alert_id, names.get(item.tenant_customer_id, ""), item.alert_type, item.alert_at, item.severity, item.alert_status, item.message or ""] for item in queryset]

    latest, _ = _latest_balances(tenant)
    energy_names = {item.energy_type_id: item.energy_type_name_fa for item in EnergyType.objects.all()}
    load_names = {item.load_band_id: item.load_band_name_fa for item in LoadBand.objects.all()}
    header = ["سطح تراز", "دوره", "نوع انرژی", "بازه بار", "خرید", "فروش", "تراز", "وضعیت", "واحد", "کامل‌بودن"]
    rows = [[item.balance_scope, item.period_jalali or "", energy_names.get(item.energy_type_id, item.energy_type_id), load_names.get(item.load_band_id, item.load_band_id), item.purchase_volume, item.sales_volume, item.balance_value, item.balance_status, item.volume_unit, item.data_completeness] for item in latest.order_by("balance_scope", "period_jalali", "energy_balance_id")]
    return header, rows


def export_rows(tenant, key, *, date_from="", date_to="", ownership=""):
    ownership = normalize_ownership(ownership)
    if key == "energy" and ownership:
        raise ValueError("تراز تجمیعی شرکت قابل تفکیک بر اساس مالکیت مشتری نیست.")
    header, rows = _export_rows(tenant, key, date_from=date_from, date_to=date_to, ownership=ownership)
    if key == "energy":
        return header, rows
    models = {"contracts": Contract, "interactions": Interaction, "followups": FollowUp, "alerts": Alert}
    if key == "customers":
        customer_by_row = {row[0]: row[0] for row in rows}
    else:
        model = models[key]
        customer_by_row = dict(model.objects.for_tenant(tenant).filter(pk__in=[row[0] for row in rows]).values_list("pk", "tenant_customer_id"))
    customers = filter_customers(TenantCustomer.objects.for_tenant(tenant), tenant)
    labels = {item.pk: LABELS[item.ownership_code] for item in customers.filter(pk__in=[value for value in customer_by_row.values() if value])}
    position = len(header) - 1 if key == "customers" else len(header)
    return header[:position] + ["نوع مالکیت"] + header[position:], [
        row[:position] + [labels.get(customer_by_row.get(row[0]), "")] + row[position:] for row in rows
    ]
