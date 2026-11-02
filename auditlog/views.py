import csv
from urllib.parse import quote

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from common.date_filters import DateFilterRangeForm, date_filter_error_response
from common.csv_safety import safe_csv_row

from accounts.access import superadmin_required
from .services import audit_export_rows, build_audit_context


FILTER_NAMES = ("query", "action", "severity", "success", "tenant_id", "date_from", "date_to")


def _filters(request):
    return {name: request.GET.get(name, "").strip() for name in FILTER_NAMES}


@superadmin_required
def audit_center(request):
    filters = _filters(request)
    date_form = DateFilterRangeForm({key: filters[key] for key in ("date_from", "date_to")})
    if not date_form.is_valid():
        return date_filter_error_response(request, date_form, return_url=reverse("auditlog:center"), from_name="date_from", to_name="date_to")
    filters.update(date_form.iso_values)
    context = build_audit_context(**filters)
    context["filters"].update(date_form.display_values)
    context["page"] = Paginator(context.pop("queryset"), 50).get_page(request.GET.get("page"))
    return render(request, "auditlog/audit_center.html", context)


@superadmin_required
def audit_export(request):
    filters = _filters(request)
    date_form = DateFilterRangeForm({key: filters[key] for key in ("date_from", "date_to")})
    if not date_form.is_valid():
        return date_filter_error_response(request, date_form, return_url=reverse("auditlog:center"), from_name="date_from", to_name="date_to")
    filters.update(date_form.iso_values)
    header, rows = audit_export_rows(**filters)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = "homaniroo-audit-log.csv"
    response["Content-Disposition"] = f"attachment; filename={filename}; filename*=UTF-8''{quote(filename)}"
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(safe_csv_row(header))
    for row in rows:
        writer.writerow(safe_csv_row(row))
    return response
