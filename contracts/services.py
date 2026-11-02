from collections import defaultdict
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Q, Subquery, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from customers.ownership import attach_ownership, filter_related, normalize as normalize_ownership

from customers.models import TenantCustomer
from energy.models import EnergyType
from .models import Contract, ContractLine, ContractMonthlyQuantity
from common.information_status import (
    assess_contract, contract_information_queryset, filter_information,
)


DIRECTION_LABELS = {"SALE": "فروش", "PURCHASE": "خرید"}
TRANSACTION_LABELS = {
    "BILATERAL": "دوجانبه",
    "EXCHANGE_FORWARD_PHYSICAL": "بورس انرژی ـ سلف فیزیکی",
    "UNKNOWN": "نامشخص",
}
STATUS_LABELS = {
    "DRAFT": "پیش‌نویس",
    "ACTIVE": "فعال",
    "EXPIRED": "منقضی",
    "TERMINATED": "خاتمه‌یافته",
    "IMPORTED": "ثبت‌شده",
}
QUALITY_LABELS = {"CLEAN": "بدون هشدار", "INCOMPLETE": "نیازمند تکمیل", "REVIEW_REQUIRED": "نیازمند بررسی"}


def _tenant_id(tenant):
    return getattr(tenant, "tenant_id", tenant)


def _counterparty_rows(tenant_id, ids=None, query=""):
    sql = """
        SELECT counterparty_id, counterparty_name, counterparty_type,
               data_quality_status
        FROM core.counterparties
        WHERE tenant_id = %s
    """
    params = [tenant_id]
    if ids is not None:
        ids = [value for value in ids if value is not None]
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        sql += f" AND counterparty_id IN ({placeholders})"
        params.extend(ids)
    if query:
        sql += " AND counterparty_name ILIKE %s"
        params.append(f"%{query}%")
    sql += " ORDER BY counterparty_name, counterparty_id"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def contracts_for_tenant(tenant):
    tenant_id = _tenant_id(tenant)
    return Contract.objects.for_tenant(tenant).annotate(
        line_count=Count("lines", filter=Q(lines__tenant_id=tenant_id), distinct=True),
        total_volume=Sum("lines__contracted_volume", filter=Q(lines__tenant_id=tenant_id)),
        total_amount=Sum("lines__amount_value", filter=Q(lines__tenant_id=tenant_id)),
    )


def filter_contracts(
    queryset,
    tenant,
    *,
    query="",
    direction="",
    transaction_type="",
    quality="",
    energy_type="",
    date_state="",
    ordering="newest",
    ownership="",
):
    tenant_id = _tenant_id(tenant)
    queryset = filter_related(queryset, tenant, ownership)
    query = (query or "").strip()
    if query:
        customer_ids = list(
            TenantCustomer.objects.for_tenant(tenant)
            .filter(industrial_unit__official_name__icontains=query)
            .values_list("tenant_customer_id", flat=True)
        )
        counterparty_ids = [
            item["counterparty_id"]
            for item in _counterparty_rows(tenant_id, query=query)
        ]
        queryset = queryset.filter(
            Q(contract_number__icontains=query)
            | Q(external_reference__icontains=query)
            | Q(source_fingerprint__icontains=query)
            | Q(tenant_customer_id__in=customer_ids)
            | Q(counterparty_id__in=counterparty_ids)
        )
    if direction in DIRECTION_LABELS:
        queryset = queryset.filter(contract_direction=direction)
    if transaction_type in TRANSACTION_LABELS:
        queryset = queryset.filter(transaction_type=transaction_type)
    queryset = filter_information(contract_information_queryset(queryset), quality)
    if energy_type:
        try:
            energy_type_id = int(energy_type)
        except (TypeError, ValueError):
            energy_type_id = None
        if energy_type_id:
            matching_contracts = ContractLine.objects.filter(
                tenant_id=tenant_id,
                energy_type_id=energy_type_id,
            ).values("contract_id")
            queryset = queryset.filter(
                contract_id__in=Subquery(matching_contracts),
            )
    today = timezone.localdate()
    if date_state == "complete":
        queryset = queryset.filter(
            contract_start_gregorian__isnull=False,
            contract_end_gregorian__isnull=False,
        )
    elif date_state == "missing":
        queryset = queryset.filter(
            Q(contract_start_gregorian__isnull=True)
            | Q(contract_end_gregorian__isnull=True)
        )
    elif date_state == "active":
        queryset = queryset.filter(
            contract_start_gregorian__lte=today,
            contract_end_gregorian__gte=today,
        )
    elif date_state == "expired":
        queryset = queryset.filter(contract_end_gregorian__lt=today)
    if ordering == "oldest":
        return queryset.order_by("contract_id").distinct()
    if ordering == "number":
        return queryset.order_by("contract_number", "contract_id").distinct()
    if ordering == "volume":
        return queryset.order_by("-total_volume", "-contract_id").distinct()
    return queryset.order_by("-contract_id").distinct()


def contract_stats(tenant):
    queryset = contract_information_queryset(Contract.objects.for_tenant(tenant))
    return {
        "total": queryset.count(),
        "sale": queryset.filter(contract_direction="SALE").count(),
        "purchase": queryset.filter(contract_direction="PURCHASE").count(),
        "review_required": filter_information(queryset, "ATTENTION").count(),
        "missing_dates": queryset.filter(
            Q(contract_start_gregorian__isnull=True)
            | Q(contract_end_gregorian__isnull=True)
        ).count(),
    }


def build_contract_list(tenant, **filters):
    queryset = filter_contracts(contracts_for_tenant(tenant), tenant, **filters)
    return {
        "queryset": queryset,
        "stats": contract_stats(tenant),
        "energy_types": list(EnergyType.objects.filter(is_active=True)),
        "filters": {
            "query": (filters.get("query") or "").strip(),
            "direction": filters.get("direction") or "",
            "transaction_type": filters.get("transaction_type") or "",
            "quality": filters.get("quality") or "",
            "energy_type": str(filters.get("energy_type") or ""),
            "date_state": filters.get("date_state") or "",
            "ordering": filters.get("ordering") or "newest",
            "ownership": normalize_ownership(filters.get("ownership", "")),
        },
    }


def _customer_map(tenant, ids):
    ids = [value for value in ids if value is not None]
    if not ids:
        return {}
    queryset = (
        TenantCustomer.objects.for_tenant(tenant)
        .filter(tenant_customer_id__in=ids)
        .select_related("industrial_unit")
    )
    return {item.tenant_customer_id: item for item in queryset}


def _line_rows(tenant, contract_ids, include_monthly=False):
    tenant_id = _tenant_id(tenant)
    if not contract_ids:
        return {}
    queryset = (
        ContractLine.objects.for_tenant(tenant)
        .filter(contract_id__in=contract_ids)
        .select_related("energy_type", "load_band")
        .order_by("contract_id", "contract_line_id")
    )
    monthly_map = defaultdict(list)
    if include_monthly:
        line_ids = list(queryset.values_list("contract_line_id", flat=True))
        quantities = ContractMonthlyQuantity.objects.filter(
            tenant_id=tenant_id,
            contract_line_id__in=line_ids,
        ).order_by("period_jalali", "monthly_quantity_id")
        for quantity in quantities:
            monthly_map[quantity.contract_line_id].append(quantity)
    result = defaultdict(list)
    for line in queryset:
        rate_value = (
            line.corrected_rate
            if line.corrected_rate is not None
            else line.base_rate
            if line.base_rate is not None
            else line.monthly_rate
        )
        result[line.contract_id].append(
            {
                "item": line,
                "rate_value": rate_value,
                "monthly_quantities": monthly_map[line.contract_line_id],
            }
        )
    return result


def _date_display(contract, field):
    jalali_value = getattr(contract, f"{field}_jalali")
    gregorian_value = getattr(contract, f"{field}_gregorian")
    return jalali_value or gregorian_value


def _lifecycle(contract):
    start = contract.contract_start_gregorian
    end = contract.contract_end_gregorian
    today = timezone.localdate()
    if not start or not end:
        return "تاریخ نامشخص", "badge-warning"
    if end < today:
        return "منقضی‌شده", "badge-muted"
    if start > today:
        return "شروع‌نشده", "badge-info"
    if end <= today + timedelta(days=30):
        return "نزدیک پایان", "badge-warning"
    return "در بازه قرارداد", "badge-success"


def enrich_contracts(tenant, contracts, include_monthly=False):
    contracts = list(contracts)
    customer_map = _customer_map(
        tenant, [item.tenant_customer_id for item in contracts]
    )
    attach_ownership(customer_map.values(), tenant)
    counterparties = {
        item["counterparty_id"]: item
        for item in _counterparty_rows(
            _tenant_id(tenant), [item.counterparty_id for item in contracts]
        )
    }
    line_map = _line_rows(
        tenant, [item.contract_id for item in contracts], include_monthly=True
    )
    rows = []
    for contract in contracts:
        lifecycle_label, lifecycle_class = _lifecycle(contract)
        lines = line_map[contract.contract_id]
        energy_labels = []
        for line in lines:
            label = line["item"].energy_type.energy_type_name_fa
            if label not in energy_labels:
                energy_labels.append(label)
        customer = customer_map.get(contract.tenant_customer_id)
        counterparty = counterparties.get(contract.counterparty_id)
        party_name = (
            customer.industrial_unit.official_name
            if customer
            else counterparty["counterparty_name"]
            if counterparty
            else "طرف قرارداد مشخص نشده"
        )
        information = assess_contract(contract, lines)
        rows.append(
            {
                "information": information,
                "item": contract,
                "display_number": contract.contract_number or f"شناسه {contract.contract_id}",
                "direction_label": DIRECTION_LABELS.get(
                    contract.contract_direction, contract.contract_direction
                ),
                "transaction_label": TRANSACTION_LABELS.get(
                    contract.transaction_type, contract.transaction_type
                ),
                "status_label": STATUS_LABELS.get(
                    contract.contract_status, contract.contract_status
                ),
                "quality_label": information.label,
                "customer": customer,
                "counterparty": counterparty,
                "party_name": party_name,
                "lines": lines,
                "energy_labels": energy_labels,
                "start_display": _date_display(contract, "contract_start"),
                "end_display": _date_display(contract, "contract_end"),
                "delivery_start_display": _date_display(contract, "delivery_start"),
                "delivery_end_display": _date_display(contract, "delivery_end"),
                "lifecycle_label": lifecycle_label,
                "lifecycle_class": lifecycle_class,
            }
        )
    return rows


def get_contract_detail(tenant, contract_id):
    contract = get_object_or_404(
        Contract.objects.for_tenant(tenant), contract_id=contract_id
    )
    row = enrich_contracts(tenant, [contract], include_monthly=True)[0]
    # Repeated numbers are a factual identification note, not proof of bad data.
    duplicate_count = (
        Contract.objects.for_tenant(tenant).filter(
            contract_direction=contract.contract_direction,
            contract_number=contract.contract_number,
        ).count() if contract.contract_number else 0
    )
    warnings = [problem.message for problem in row["information"].issues]
    return {"contract": contract, "row": row, "warnings": warnings, "duplicate_count": duplicate_count}
