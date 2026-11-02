import csv
from urllib.parse import quote

from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse

from common.date_filters import DateFilterRangeForm, date_filter_error_response
from common.csv_safety import safe_csv_row

from accounts.access import role_required
from common.tenant_access import tenant_required
from .services import EXPORT_KEYS, build_management_report, export_rows
from customers.ownership import normalize as normalize_ownership


REPORT_ROLES = ("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")
REPORT_LABELS = {
    "customers": "مشتریان",
    "contracts": "قراردادها",
    "interactions": "تماس‌ها",
    "followups": "پیگیری‌ها",
    "alerts": "هشدارها",
    "energy": "تراز انرژی",
}


@tenant_required
@role_required(*REPORT_ROLES)
def report_center(request):
    date_form = DateFilterRangeForm({"date_from": request.GET.get("from", ""), "date_to": request.GET.get("to", "")})
    if not date_form.is_valid():
        return date_filter_error_response(request, date_form, return_url=reverse("reports:center"), from_name="from", to_name="to")
    arguments = dict(date_form.iso_values)
    ownership = normalize_ownership(request.GET.get("ownership", ""))
    if ownership:
        arguments["ownership"] = ownership
    context = build_management_report(request.tenant, **arguments)
    context["filters"].update(date_form.display_values)
    context["report_labels"] = REPORT_LABELS
    return render(request, "reports/report_center.html", context)


@tenant_required
@role_required("COMPANY_ADMIN")
def export_csv(request, report_key):
    if report_key not in EXPORT_KEYS:
        raise Http404("گزارش درخواستی وجود ندارد.")
    date_form = DateFilterRangeForm({"date_from": request.GET.get("from", ""), "date_to": request.GET.get("to", "")})
    if not date_form.is_valid():
        return date_filter_error_response(request, date_form, return_url=reverse("reports:center"), from_name="from", to_name="to")
    arguments = dict(date_form.iso_values)
    ownership = normalize_ownership(request.GET.get("ownership", ""))
    if ownership:
        arguments["ownership"] = ownership
    try:
        header, rows = export_rows(request.tenant, report_key, **arguments)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"homaniroo-{report_key}.csv"
    response["Content-Disposition"] = f"attachment; filename={filename}; filename*=UTF-8''{quote(filename)}"
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(safe_csv_row(header))
    for row in rows:
        writer.writerow(safe_csv_row(row))
    return response
