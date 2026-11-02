from decimal import Decimal

from django.db.models import Count, Max, Q, Sum

from contracts.models import Contract, ContractLine
from .models import EnergyBalance, EnergyType, LoadBand


SCOPE_LABELS = {
    "CONTRACT_TOTAL": "جمع کل قراردادها",
    "MONTHLY_AVAILABLE": "اطلاعات ماهانه موجود",
}
STATUS_LABELS = {
    "SURPLUS": "مازاد",
    "DEFICIT": "کسری",
    "BALANCED": "متوازن",
}
STATUS_CLASSES = {
    "SURPLUS": "success",
    "DEFICIT": "danger",
    "BALANCED": "muted",
}
COMPLETENESS_LABELS = {
    "COMPLETE": "کامل",
    "INCOMPLETE_PURCHASE": "خرید ناقص",
    "INCOMPLETE_SALES": "فروش ناقص",
    "INCOMPLETE_BOTH": "خرید و فروش ناقص",
}


def _tenant_id(tenant):
    return getattr(tenant, "tenant_id", tenant)


def latest_run_id(tenant):
    return EnergyBalance.objects.for_tenant(tenant).aggregate(
        value=Max("validation_run_id")
    )["value"]


def latest_balances(tenant):
    run_id = latest_run_id(tenant)
    if run_id is None:
        return EnergyBalance.objects.none(), None
    return (
        EnergyBalance.objects.for_tenant(tenant).filter(validation_run_id=run_id),
        run_id,
    )


def filter_balances(
    queryset,
    *,
    scope="CONTRACT_TOTAL",
    period="",
    energy_type="",
    load_band="",
    status="",
    completeness="",
):
    if scope not in SCOPE_LABELS:
        scope = "CONTRACT_TOTAL"
    queryset = queryset.filter(balance_scope=scope)
    if scope == "MONTHLY_AVAILABLE" and period:
        queryset = queryset.filter(period_jalali=period)
    for value, field in (
        (energy_type, "energy_type_id"),
        (load_band, "load_band_id"),
    ):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = None
        if value:
            queryset = queryset.filter(**{field: value})
    if status in STATUS_LABELS:
        queryset = queryset.filter(balance_status=status)
    if completeness in COMPLETENESS_LABELS:
        queryset = queryset.filter(data_completeness=completeness)
    return queryset.order_by(
        "period_jalali", "energy_type_id", "load_band_id", "energy_balance_id"
    )


def _catalog_maps():
    energy_types = list(EnergyType.objects.filter(is_active=True))
    load_bands = list(LoadBand.objects.filter(is_active=True))
    return (
        energy_types,
        load_bands,
        {item.energy_type_id: item for item in energy_types},
        {item.load_band_id: item for item in load_bands},
    )


def _aggregate_summary(queryset):
    data = queryset.aggregate(
        purchase=Sum("purchase_volume"),
        sales=Sum("sales_volume"),
        balance=Sum("balance_value"),
        rows=Count("energy_balance_id"),
        surplus=Count("energy_balance_id", filter=Q(balance_status="SURPLUS")),
        deficit=Count("energy_balance_id", filter=Q(balance_status="DEFICIT")),
        balanced=Count("energy_balance_id", filter=Q(balance_status="BALANCED")),
        incomplete=Count(
            "energy_balance_id", filter=~Q(data_completeness="COMPLETE")
        ),
    )
    for key in ("purchase", "sales", "balance"):
        data[key] = data[key] or Decimal("0")
    raw_balance = data["balance"]
    if data["rows"] == 0:
        data.update(balance_label="فاقد داده", balance_class="muted")
    elif raw_balance < 0:
        data.update(balance_label="کسری", balance_class="danger")
    elif raw_balance > 0:
        data.update(balance_label="مازاد", balance_class="success")
    else:
        data.update(balance_label="متوازن", balance_class="muted")
    data["balance_absolute"] = abs(raw_balance)
    return data


def transaction_summary(tenant):
    tenant_id = _tenant_id(tenant)
    contracts = Contract.objects.for_tenant(tenant)
    lines = ContractLine.objects.for_tenant(tenant).filter(
        contract__tenant_id=tenant_id
    )
    result = {}
    for direction, key in (("PURCHASE", "purchase"), ("SALE", "sale")):
        direction_lines = lines.filter(contract__contract_direction=direction)
        volume = direction_lines.aggregate(value=Sum("contracted_volume"))["value"]
        result[key] = {
            "contracts": contracts.filter(contract_direction=direction).count(),
            "lines": direction_lines.count(),
            "known_volume": volume or Decimal("0"),
            "missing_volume": direction_lines.filter(
                contracted_volume__isnull=True
            ).count(),
        }
    return result


def _enrich_rows(queryset, energy_map, load_map):
    rows = []
    for item in queryset:
        rows.append(
            {
                "item": item,
                "energy_type": energy_map.get(item.energy_type_id),
                "load_band": load_map.get(item.load_band_id),
                "scope_label": SCOPE_LABELS.get(item.balance_scope, item.balance_scope),
                "status_label": STATUS_LABELS.get(
                    item.balance_status, item.balance_status
                ),
                "status_class": STATUS_CLASSES.get(item.balance_status, "muted"),
                "completeness_label": COMPLETENESS_LABELS.get(
                    item.data_completeness, item.data_completeness
                ),
            }
        )
    return rows


def _monthly_series(queryset, energy_type="", load_band=""):
    monthly = queryset.filter(balance_scope="MONTHLY_AVAILABLE")
    try:
        energy_type = int(energy_type)
    except (TypeError, ValueError):
        energy_type = None
    try:
        load_band = int(load_band)
    except (TypeError, ValueError):
        load_band = None
    if energy_type:
        monthly = monthly.filter(energy_type_id=energy_type)
    if load_band:
        monthly = monthly.filter(load_band_id=load_band)
    values = list(
        monthly.values("period_jalali")
        .annotate(
            purchase=Sum("purchase_volume"),
            sales=Sum("sales_volume"),
            balance=Sum("balance_value"),
            rows=Count("energy_balance_id"),
            incomplete=Count(
                "energy_balance_id", filter=~Q(data_completeness="COMPLETE")
            ),
        )
        .order_by("period_jalali")
    )
    maximum = max(
        [abs(item[metric] or Decimal("0")) for item in values for metric in ("purchase", "sales")]
        or [Decimal("0")]
    )
    for item in values:
        for metric in ("purchase", "sales"):
            value = item[metric] or Decimal("0")
            item[f"{metric}_width"] = (
                max(2, int(abs(value) * 100 / maximum)) if maximum else 0
            )
        item["balance"] = item["balance"] or Decimal("0")
        item["status_label"] = "کسری" if item["balance"] < 0 else "مازاد" if item["balance"] > 0 else "متوازن"
        item["status_class"] = "danger" if item["balance"] < 0 else "success" if item["balance"] > 0 else "muted"
    return values


def build_energy_trading_context(
    tenant,
    *,
    scope="CONTRACT_TOTAL",
    period="",
    energy_type="",
    load_band="",
    status="",
    completeness="",
):
    latest, run_id = latest_balances(tenant)
    energy_types, load_bands, energy_map, load_map = _catalog_maps()
    periods = list(
        latest.filter(balance_scope="MONTHLY_AVAILABLE")
        .exclude(period_jalali__isnull=True)
        .values_list("period_jalali", flat=True)
        .distinct()
        .order_by("period_jalali")
    )
    filtered = filter_balances(
        latest,
        scope=scope,
        period=period,
        energy_type=energy_type,
        load_band=load_band,
        status=status,
        completeness=completeness,
    )
    contract_total = latest.filter(balance_scope="CONTRACT_TOTAL")
    latest_calculated_at = latest.aggregate(value=Max("calculated_at"))["value"]
    return {
        "latest_run": run_id,
        "latest_calculated_at": latest_calculated_at,
        "summary": _aggregate_summary(contract_total),
        "filtered_summary": _aggregate_summary(filtered),
        "transactions": transaction_summary(tenant),
        "rows": _enrich_rows(filtered, energy_map, load_map),
        "monthly_series": _monthly_series(latest, energy_type, load_band),
        "energy_types": energy_types,
        "load_bands": load_bands,
        "periods": periods,
        "filters": {
            "scope": scope if scope in SCOPE_LABELS else "CONTRACT_TOTAL",
            "period": period or "",
            "energy_type": str(energy_type or ""),
            "load_band": str(load_band or ""),
            "status": status or "",
            "completeness": completeness or "",
        },
    }
