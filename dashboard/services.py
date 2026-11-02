from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from common.information_status import customer_information_queryset, contract_information_queryset
from contracts.models import Contract
from customers.models import TenantCustomer
from energy.models import EnergyBalance
from interactions.models import Alert, FollowUp, Interaction


MISSED_CALL_TERMS = (
    "NO_ANSWER",
    "جواب نداد",
    "پاسخ نداد",
    "عدم پاسخ",
    "در دسترس نبود",
    "در دستر نبود",
)


def _format_decimal(value):
    if value is None:
        return "۰"
    value = Decimal(value)
    if value == value.to_integral():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _empty_context():
    return {
        "dashboard_ready": False,
        "customers": {},
        "contracts": {},
        "follow_ups": {},
        "interactions": {},
        "alerts": {},
        "energy": {},
        "quality": {},
    }


def build_dashboard_context(tenant):
    """Return tenant-scoped, aggregate-only metrics for the management dashboard."""
    if tenant is None:
        return _empty_context()

    today = timezone.localdate()
    next_month = today + timedelta(days=30)
    current_tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    tomorrow_start = today_start + timedelta(days=1)
    next_week_end = today_start + timedelta(days=8)

    customer_qs = customer_information_queryset(TenantCustomer.objects.for_tenant(tenant))
    customer_data = customer_qs.aggregate(
        total=Count("tenant_customer_id"),
        active=Count("tenant_customer_id", filter=Q(customer_status="ACTIVE")),
        leads=Count("tenant_customer_id", filter=Q(customer_status="LEAD")),
        review_required=Count(
            "tenant_customer_id", filter=Q(info_incomplete=True) | Q(info_review=True)
        ),
    )

    contract_qs = contract_information_queryset(Contract.objects.for_tenant(tenant))
    contract_data = contract_qs.aggregate(
        total=Count("contract_id"),
        sales=Count("contract_id", filter=Q(contract_direction="SALE")),
        purchases=Count("contract_id", filter=Q(contract_direction="PURCHASE")),
        dated=Count(
            "contract_id", filter=Q(contract_end_gregorian__isnull=False)
        ),
        active=Count(
            "contract_id",
            filter=(
                Q(contract_end_gregorian__gte=today)
                & (
                    Q(contract_start_gregorian__isnull=True)
                    | Q(contract_start_gregorian__lte=today)
                )
            ),
        ),
        expiring=Count(
            "contract_id",
            filter=Q(
                contract_end_gregorian__gte=today,
                contract_end_gregorian__lte=next_month,
            ),
        ),
        missing_dates=Count(
            "contract_id",
            filter=Q(contract_start_gregorian__isnull=True) | Q(contract_end_gregorian__isnull=True),
        ),
        review_required=Count(
            "contract_id", filter=Q(info_incomplete=True) | Q(info_review=True)
        ),
    )
    contract_dates_available = contract_data["dated"] > 0

    follow_up_qs = FollowUp.objects.for_tenant(tenant).filter(follow_up_status="OPEN")
    follow_up_data = follow_up_qs.aggregate(
        open=Count("follow_up_id"),
        today=Count(
            "follow_up_id",
            filter=Q(due_at__gte=today_start, due_at__lt=tomorrow_start),
        ),
        overdue=Count("follow_up_id", filter=Q(due_at__lt=today_start)),
        missing_date=Count("follow_up_id", filter=Q(due_at__isnull=True)),
        next_seven_days=Count(
            "follow_up_id",
            filter=Q(due_at__gte=tomorrow_start, due_at__lt=next_week_end),
        ),
    )

    missed_filter = Q()
    for term in MISSED_CALL_TERMS:
        missed_filter |= Q(interaction_status__icontains=term)
    interaction_qs = Interaction.objects.for_tenant(tenant)
    interaction_data = interaction_qs.aggregate(
        total=Count("interaction_id"),
        missed=Count("interaction_id", filter=missed_filter),
    )

    alert_qs = Alert.objects.for_tenant(tenant)
    alert_data = alert_qs.aggregate(
        open=Count("alert_id", filter=Q(alert_status="OPEN")),
        total=Count("alert_id"),
    )

    balance_qs = EnergyBalance.objects.for_tenant(tenant)
    latest_run = balance_qs.aggregate(value=Max("validation_run_id"))["value"]
    if latest_run is not None:
        balance_qs = balance_qs.filter(
            validation_run_id=latest_run,
            balance_scope="CONTRACT_TOTAL",
        )
        energy_data = balance_qs.aggregate(
            purchase=Sum("purchase_volume"),
            sales=Sum("sales_volume"),
            balance=Sum("balance_value"),
            deficit_items=Count(
                "energy_balance_id", filter=Q(balance_status="DEFICIT")
            ),
            balanced_items=Count(
                "energy_balance_id", filter=Q(balance_status="BALANCED")
            ),
            rows=Count("energy_balance_id"),
        )
    else:
        energy_data = {
            "purchase": None,
            "sales": None,
            "balance": None,
            "deficit_items": 0,
            "balanced_items": 0,
            "rows": 0,
        }

    raw_balance = energy_data["balance"] or Decimal("0")
    if energy_data["rows"] == 0:
        balance_label = "فاقد داده"
        balance_tone = "muted"
    elif raw_balance < 0:
        balance_label = "کسری"
        balance_tone = "danger"
    elif raw_balance > 0:
        balance_label = "مازاد"
        balance_tone = "success"
    else:
        balance_label = "متوازن"
        balance_tone = "success"

    return {
        "dashboard_ready": True,
        "dashboard_date": today,
        "customers": customer_data,
        "contracts": {
            **contract_data,
            "dates_available": contract_dates_available,
            "active_display": contract_data["active"] if contract_dates_available else None,
            "expiring_display": contract_data["expiring"] if contract_dates_available else None,
        },
        "follow_ups": follow_up_data,
        "interactions": interaction_data,
        "alerts": alert_data,
        "energy": {
            **energy_data,
            "purchase_display": _format_decimal(energy_data["purchase"]),
            "sales_display": _format_decimal(energy_data["sales"]),
            "balance_display": _format_decimal(abs(raw_balance)),
            "balance_label": balance_label,
            "balance_tone": balance_tone,
            "latest_run": latest_run,
        },
        "quality": {
            "customer_review": customer_data["review_required"],
            "contract_review": contract_data["review_required"],
            "contracts_missing_dates": contract_data["missing_dates"],
            "followups_missing_dates": follow_up_data["missing_date"],
        },
    }
