"""CSV boundary safety; all database records below belong to pytest's test DB."""
import copy
import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from common.csv_safety import safe_csv_cell, safe_csv_row
from reports import views as report_views
from reports.tests import report_tables, active_customer_tables, prospective_customer_tables
from auditlog import views as audit_views


@pytest.mark.parametrize("value", [
    "=1+1", "+1+1", "-1+1", "@SUM(A1:A2)",
    '=HYPERLINK("https://invalid.example/","label")',
    "=cmd|' /C harmless-example'!A0", "+989121234567", "-12.5", "-0", "+0",
    " =1+1", "  +SUM(1,2)", "\t=1+1", "\r=1+1", "\n=1+1",
    "\x00=1+1", "\x1f@SUM(A1:A2)", "\x7f-1+1", "\v=1", "\f=1",
    "\ufeff=1", "\u200b+1", "\u200c-1", "\u200d@SUM(A1:A2)", "\u2060=1",
    "\u00a0=1", "\u202f+1", "\u3000@SUM(A1:A2)", "\u202e=1", "\u2066+1",
    "＝1+1", "＋1+1", "－1+1", "＠SUM(A1:A2)",
    "\tordinary text", "\nیادداشت", "\r", "\t", "\x00", "\x7f",
])
def test_dangerous_text_gets_one_export_only_prefix(value):
    result = safe_csv_cell(value)
    assert result == "'" + value
    assert safe_csv_cell(result) == result


@pytest.mark.parametrize("value", [
    "", "هما نیرو", "انرژی فرزان", "متن فارسی و English", "مشتری ۱۲۳",
    "1405/08/10", "2026-11-01", "09121234567", "00123", "0", "12.5",
    " a normal name", "'already text", "  'already text", "hello=1", "note @ address",
    'text,with,"quotes"', "خط اول\nخط دوم", "متن\tداخلی", " متن عادی",
])
def test_safe_text_stays_byte_for_byte(value):
    assert safe_csv_cell(value) == value


@pytest.mark.parametrize("value", [
    0, 1, -1, -123456, 0.0, -0.0, -12.5, 1e-10, False, True,
    Decimal("0"), Decimal("-0.00"), Decimal("-659500.250000"),
    Decimal("12345678901234567890.123456"), Decimal("-1E-20"),
])
def test_typed_numbers_keep_value_type_and_precision(value):
    result = safe_csv_cell(value)
    assert result is value
    assert type(result) is type(value)


def test_missing_is_not_zero_and_dates_keep_iso_format():
    assert safe_csv_cell(None) == ""
    for value in (date(2026, 11, 1), datetime(2026, 11, 1, 12, 30, tzinfo=timezone.utc), time(12, 30)):
        assert safe_csv_cell(value) == value.isoformat()
    assert safe_csv_row([None, 0, Decimal("0"), ""]) == ["", 0, Decimal("0"), ""]


def test_row_is_new_and_input_not_mutated():
    original = ["=1+1", Decimal("-12.50"), None, "نام"]
    before = copy.deepcopy(original)
    result = safe_csv_row(original)
    assert result is not original and original == before
    assert result == ["'=1+1", Decimal("-12.50"), "", "نام"]
    assert safe_csv_row(iter(original)) == result


def test_unknown_object_cannot_bypass_text_check():
    class TextValue:
        def __str__(self): return "=1+1"
    assert safe_csv_cell(TextValue()) == "'=1+1"


def request(path="/", query=None):
    result = RequestFactory().get(path, query or {})
    result.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
    result.tenant = SimpleNamespace(tenant_id=17, display_name="شرکت آزمون")
    return result


def parsed(response):
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))


@pytest.mark.parametrize("key", ["customers", "contracts", "interactions", "followups", "alerts", "energy", "audit"])
def test_every_csv_route_escapes_text_and_preserves_numeric_cells_and_structure(key):
    header = ["=header", "عنوان", "عدد", "صفر", "خالی", "تاریخ", "علامت‌ها"]
    rows = [["=1+1", "+989121234567", Decimal("-659500.250000"), 0, None,
             date(2026, 11, 1), 'normal,"quote"\nnext line'],
            ["\ufeff@SUM(A1:A2)", "-12.5", -12.5, Decimal("0"), "", None, "متن فارسی"]]
    original = copy.deepcopy((header, rows))
    if key == "audit":
        with patch.object(audit_views, "audit_export_rows", return_value=(header, rows)):
            response = audit_views.audit_export(request())
        filename = "homaniroo-audit-log.csv"
    else:
        with patch.object(report_views, "export_rows", return_value=(header, rows)) as export:
            response = report_views.export_csv(request(), key)
        assert export.call_args.args[0].tenant_id == 17
        filename = f"homaniroo-{key}.csv"
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert filename in response["Content-Disposition"]
    output = parsed(response)
    assert len(output) == 3 and all(len(row) == 7 for row in output)
    assert output[0] == ["'=header", *header[1:]]
    assert output[1] == ["'=1+1", "'+989121234567", "-659500.250000", "0", "", "2026-11-01", rows[0][-1]]
    assert output[2] == ["'\ufeff@SUM(A1:A2)", "'-12.5", "-12.5", "0", "", "", "متن فارسی"]
    assert (header, rows) == original


def test_csv_escaping_does_not_split_quoted_field_into_extra_columns():
    value = 'normal,=1+1,"quoted"\r\n@SUM(A1:A2)'
    out = StringIO(newline=""); writer = csv.writer(out)
    writer.writerow(safe_csv_row([value, "=1+1", "tail"]))
    assert list(csv.reader(StringIO(out.getvalue(), newline=""))) == [[value, "'=1+1", "tail"]]


def test_report_filter_arguments_remain_unchanged():
    query = {"from": "1405/07/25", "to": "1405/07/25", "ownership": "PRIVATE"}
    with patch.object(report_views, "export_rows", return_value=(["عنوان"], [["=1"]])) as export:
        response = report_views.export_csv(request(query=query), "customers")
    assert response.status_code == 200
    assert export.call_args.kwargs == {"date_from": "2026-10-17", "date_to": "2026-10-17", "ownership": "PRIVATE"}


def test_audit_filter_arguments_remain_unchanged():
    query = {"date_from": "1405/07/25", "date_to": "1405/07/25", "tenant_id": "17",
             "query": "=input", "success": "yes", "action": "SYSTEM", "severity": "IMPORTANT"}
    with patch.object(audit_views, "audit_export_rows", return_value=(["شرح"], [["=1"]])) as export:
        response = audit_views.audit_export(request(query=query))
    assert response.status_code == 200
    assert export.call_args.kwargs == {**query, "date_from": "2026-10-17", "date_to": "2026-10-17"}


@pytest.mark.parametrize("kind", ["report", "audit"])
def test_unauthorized_request_cannot_reach_export_data(kind):
    req = request()
    req.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=False,
                              profile=None, groups=SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(exists=lambda: False)))
    module, name = (report_views, "export_rows") if kind == "report" else (audit_views, "audit_export_rows")
    with patch.object(module, name) as export, pytest.raises(PermissionDenied):
        if kind == "report": report_views.export_csv(req, "customers")
        else: audit_views.audit_export(req)
    export.assert_not_called()


@pytest.mark.django_db
def test_actual_audit_record_is_escaped_only_in_output_not_database():
    from auditlog.models import AuditLog
    event = AuditLog.objects.create(tenant_id=17, username="+export_user", action="SYSTEM",
                                   description="=1+1", entity_id="-text-id", request_path="/safe/path/")
    before = AuditLog.objects.filter(pk=event.pk).values().get()
    response = audit_views.audit_export(request(query={"query": "=1+1", "tenant_id": "17"}))
    row, = parsed(response)[1:]
    assert row[1] == "'+export_user" and row[5] == "'-text-id" and row[6] == "'=1+1"
    assert AuditLog.objects.filter(pk=event.pk).values().get() == before


@pytest.mark.django_db
def test_actual_customer_csv_is_tenant_scoped_and_does_not_rewrite_source(report_tables):
    from customers.models import IndustrialUnit, TenantCustomer, CustomerContact
    first = IndustrialUnit.objects.create(official_name="=1+1", normalized_name="=1+1")
    customer = TenantCustomer.objects.create(tenant_id=17, industrial_unit=first, customer_status="ACTIVE")
    contact = CustomerContact.objects.create(tenant_id=17, tenant_customer=customer,
        contact_person="@SUM(A1:A2)", mobile_number="+989121234567", is_primary=True)
    other = IndustrialUnit.objects.create(official_name="OTHER_COMPANY_ONLY", normalized_name="other company")
    TenantCustomer.objects.create(tenant_id=18, industrial_unit=other, customer_status="ACTIVE")
    before_unit = IndustrialUnit.objects.filter(pk=first.pk).values().get()
    before_contact = CustomerContact.objects.filter(pk=contact.pk).values().get()
    response = report_views.export_csv(request(), "customers")
    output = parsed(response)
    header = output[0]
    row, = output[1:]
    assert row[header.index("نام مشتری")] == "'=1+1"
    assert row[header.index("شخص رابط")] == "'@SUM(A1:A2)"
    assert row[header.index("موبایل")] == "'+989121234567"
    assert "OTHER_COMPANY_ONLY" not in response.content.decode("utf-8-sig")
    assert IndustrialUnit.objects.filter(pk=first.pk).values().get() == before_unit
    assert CustomerContact.objects.filter(pk=contact.pk).values().get() == before_contact
