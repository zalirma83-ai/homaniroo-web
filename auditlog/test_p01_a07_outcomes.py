"""A07 targeted tests. No live users, roles or business records are modified."""
import csv
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.test import RequestFactory

from tenants.models import Tenant
from .admin import AuditLogAdmin, AuditResultFilter
from .middleware import AuditTrailMiddleware
from .models import AuditLog
from .services import (audit_export_rows, build_audit_context, filter_audit_logs,
                       record_audit_event)
from .tests import audit_core_tables
from .views import audit_center, audit_export


def request(method="POST", path="/sample/", data=None):
    req = getattr(RequestFactory(), method.lower())(path, data or {})
    req.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True,
                              pk=101, get_username=lambda: "a07_test_actor",
                              get_full_name=lambda: "کاربر آزمایشی", username="a07_test_actor")
    req.tenant = SimpleNamespace(tenant_id=17)
    req.tenant_choices = []
    req.resolver_match = SimpleNamespace(view_name="sample:update")
    return req


@pytest.mark.parametrize("status", [200, 201, 204, 302, 400, 403, 409, 500])
def test_http_response_never_asserts_business_success_or_stores_payload(status):
    req = request(data={"password": "never-store", "notes": "private-form-text"})
    response = HttpResponse("private-response-text", status=status)
    with patch("auditlog.middleware.record_audit_event") as record:
        assert AuditTrailMiddleware(lambda unused: response)(req) is response
    args = record.call_args.kwargs
    assert args["action"] == "SYSTEM" and args["entity_type"] == "http_request"
    assert args["metadata"] == {"audit_scope": "http_request", "business_outcome": "UNKNOWN",
                                "status_code": status, "view_name": "sample:update"}
    assert args["success"] is (status < 400)  # Transport only, not a database outcome.
    assert args["severity"] == ("NORMAL" if status < 400 else "IMPORTANT")
    assert "نتیجهٔ عملیات نامشخص" in args["description"]
    text = str(args["metadata"]) + args["description"]
    assert "never-store" not in text and "private-form-text" not in text and "private-response-text" not in text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_all_mutating_verbs_are_request_events_not_create_or_delete_claims(method):
    req = request(method)
    with patch("auditlog.middleware.record_audit_event") as record:
        AuditTrailMiddleware(lambda unused: HttpResponse(status=302))(req)
    assert record.call_args.kwargs["action"] == "SYSTEM"


@pytest.mark.parametrize("kind", ["GET", "HEAD", "anonymous", "login", "logout"])
def test_existing_exclusions_are_kept(kind):
    req = request(kind if kind in {"GET", "HEAD"} else "POST",
                  "/account/login/" if kind == "login" else "/account/logout/" if kind == "logout" else "/sample/")
    if kind == "anonymous": req.user = SimpleNamespace(is_authenticated=False)
    with patch("auditlog.middleware.record_audit_event") as record:
        AuditTrailMiddleware(lambda unused: HttpResponse())(req)
    record.assert_not_called()


def new_http(status=200):
    return AuditLog.objects.create(action="SYSTEM", entity_type="http_request", tenant_id=17,
        description=f"پاسخ HTTP {status}", request_path="/sample/", request_method="POST", success=status < 400,
        metadata={"audit_scope": "http_request", "business_outcome": "UNKNOWN", "status_code": status, "view_name": "sample:update"})


def legacy_http(method="POST", status=200, **overrides):
    data = dict(action="DELETE" if method == "DELETE" else "CREATE_OR_UPDATE", tenant_id=17,
        entity_type="", entity_id="", description=f"{method} /sample/", request_path="/sample/",
        request_method=method, success=status < 400, metadata={"status_code": status, "view_name": "sample:update"})
    data.update(overrides)
    return AuditLog.objects.create(**data)


@pytest.fixture
def sample_events(audit_core_tables):
    ok = AuditLog.objects.create(action="CREATE_OR_UPDATE", entity_type="customer", entity_id="7", tenant_id=17, success=True)
    failed = AuditLog.objects.create(action="CREATE_OR_UPDATE", entity_type="customer", entity_id="8", tenant_id=17, success=False)
    return SimpleNamespace(ok=ok, failed=failed, http_ok=new_http(), http_error=new_http(409),
                           legacy_ok=legacy_http(), legacy_error=legacy_http("DELETE", 500))


@pytest.mark.django_db
def test_invalid_form_with_http_200_creates_only_unknown_request_event(audit_core_tables):
    req = request()
    AuditTrailMiddleware(lambda unused: HttpResponse("invalid form", status=200))(req)
    item, = filter_audit_logs()
    assert item.audit_result_scope == "http"
    assert item.audit_result_class == "badge-muted"
    assert "نتیجهٔ عملیات نامشخص" in item.audit_result_label
    assert filter_audit_logs(success="yes").count() == 0
    assert filter_audit_logs(success="http_ok").count() == 1


@pytest.mark.django_db
def test_explicit_committed_event_and_generic_http_do_not_double_count_mutation(audit_core_tables):
    req = request()
    def view(unused):
        with transaction.atomic():
            tenant = Tenant.objects.create(tenant_code="A07_OK", legal_name="test", display_name="آزمایشی")
            event = record_audit_event(action="CREATE_OR_UPDATE", request=req, entity_type="tenant",
                entity_id=tenant.pk, metadata={"operation": "a07_test_commit"}, success=True)
            assert event is not None
        return HttpResponse(status=302)
    AuditTrailMiddleware(view)(req)
    totals = build_audit_context()["totals"]
    assert totals["total"] == 2 and totals["successful"] == 1 and totals["mutations"] == 1
    assert totals["http_requests"] == 1 and totals["failed"] == 0
    assert filter_audit_logs(success="yes").get().entity_type == "tenant"


@pytest.mark.django_db
def test_rolled_back_operation_returning_200_is_not_reported_as_success(audit_core_tables):
    req = request()
    def view(unused):
        try:
            with transaction.atomic():
                tenant = Tenant.objects.create(tenant_code="A07_ROLLBACK", legal_name="test", display_name="آزمایشی")
                record_audit_event(action="CREATE_OR_UPDATE", request=req, entity_type="tenant", entity_id=tenant.pk)
                raise ValidationError("simulated operation failure")
        except ValidationError:
            return HttpResponse("invalid form", status=200)
    AuditTrailMiddleware(view)(req)
    assert not Tenant.objects.filter(tenant_code="A07_ROLLBACK").exists()
    totals = build_audit_context()["totals"]
    assert totals["successful"] == totals["mutations"] == 0
    assert totals["http_requests"] == totals["total"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_legacy_signature_is_unknown_without_rewriting_original_rows(audit_core_tables, method):
    old = legacy_http(method)
    before = AuditLog.objects.filter(pk=old.pk).values().get()
    item = filter_audit_logs().get(pk=old.pk)
    assert item.audit_result_scope == "legacy_http" and item.audit_result_class == "badge-muted"
    assert item.audit_result_label == "قدیمی؛ نتیجهٔ عملیات نامشخص"
    assert AuditLog.objects.filter(pk=old.pk).values().get() == before
    assert filter_audit_logs(success="yes").count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("overrides", [
    {"entity_type": "customer"}, {"entity_id": "42"}, {"description": "ویرایش ثبت‌شده مشتری"},
    {"metadata": {"status_code": 200}}, {"metadata": {"view_name": "sample:update"}},
    {"action": "SYSTEM"}, {"request_method": "GET"}, {"request_path": ""},
])
def test_explicit_or_dissimilar_old_records_are_not_guessed_as_http(audit_core_tables, overrides):
    event = legacy_http(**overrides)
    assert filter_audit_logs().get(pk=event.pk).audit_result_scope == "event"


@pytest.mark.django_db
@pytest.mark.parametrize("value,fields", [
    ("yes", ["ok"]), ("no", ["failed"]), ("http_ok", ["http_ok"]),
    ("http_error", ["http_error"]), ("legacy_http", ["legacy_ok", "legacy_error"]),
])
def test_result_filters_are_scope_aware(sample_events, value, fields):
    expected = {getattr(sample_events, name).pk for name in fields}
    assert set(filter_audit_logs(success=value).values_list("pk", flat=True)) == expected


@pytest.mark.django_db
def test_totals_do_not_treat_old_or_new_http_as_business_result(sample_events):
    totals = build_audit_context()["totals"]
    assert totals["total"] == 6
    assert totals["successful"] == totals["failed"] == totals["mutations"] == 1
    assert totals["http_requests"] == totals["legacy_http"] == 2
    assert totals["total"] == totals["successful"] + totals["failed"] + totals["http_requests"] + totals["legacy_http"]


@pytest.mark.django_db
def test_csv_uses_same_scope_and_result_labels_with_existing_columns(sample_events):
    before = list(AuditLog.objects.values())
    header, rows = audit_export_rows()
    assert len(header) == 12 and len(rows) == 6 and all(len(row) == 12 for row in rows)
    by_result = {row[10]: row for row in rows}
    assert "موفق (ثبت‌شده)" in by_result and "ناموفق (ثبت‌شده)" in by_result
    assert "HTTP بدون خطا؛ نتیجهٔ عملیات نامشخص" in by_result
    assert "خطای HTTP؛ نتیجهٔ عملیات نامشخص" in by_result
    assert "قدیمی؛ نتیجهٔ عملیات نامشخص" in by_result
    assert len(audit_export_rows(success="yes")[1]) == 1
    assert list(AuditLog.objects.values()) == before


@pytest.mark.django_db
def test_real_csv_retains_a06_protection_and_filters(audit_core_tables):
    record_audit_event(action="CREATE_OR_UPDATE", entity_type="customer", tenant=17,
                       description="=export-text", user=request().user)
    req = request("GET", "/system/audit/export/", {"query": "=export-text", "tenant_id": "17", "success": "yes"})
    response = audit_export(req)
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1][6] == "'=export-text" and rows[1][10] == "موفق (ثبت‌شده)"
    assert len(rows) == 2 and len(rows[0]) == len(rows[1]) == 12
    assert AuditLog.objects.get().description == "=export-text"


@pytest.mark.django_db
def test_company_dates_and_pagination_remain_applied(sample_events):
    AuditLog.objects.create(action="SYSTEM", tenant_id=18, description="other company")
    from django.utils import timezone
    today = timezone.localdate().isoformat()
    rows = filter_audit_logs(tenant_id="17", date_from=today, date_to=today)
    assert rows.count() == 6
    page = Paginator(rows, 2).get_page(2)
    assert len(page.object_list) == 2 and page.paginator.num_pages == 3
    assert all(row.tenant_id == 17 for row in page)


@pytest.mark.django_db
def test_audit_page_shows_unknown_results_not_green_success(sample_events):
    req = request("GET", "/system/audit/", {"success": "http_ok"})
    response = audit_center(req)
    html = response.content.decode()
    assert response.status_code == 200
    assert "HTTP بدون خطا؛ نتیجهٔ عملیات نامشخص" in html
    assert 'badge badge-muted' in html
    assert "پاسخ HTTP موفق، ذخیرهٔ واقعی عملیات را ثابت نمی‌کند" in html
    assert 'value="http_ok" selected' in html


@pytest.mark.django_db
def test_admin_shares_result_semantics_and_remains_read_only(sample_events):
    model_admin = AuditLogAdmin(AuditLog, admin.site)
    req = request("GET")
    rows = model_admin.get_queryset(req)
    event = rows.get(pk=sample_events.http_ok.pk)
    assert "عملیات نامشخص" in model_admin.event_result(event)
    assert model_admin.event_action(event) == "درخواست HTTP"
    assert "success" not in model_admin.list_display and "success" in model_admin.exclude
    assert not model_admin.has_add_permission(req)
    assert not model_admin.has_change_permission(req, event)
    assert not model_admin.has_delete_permission(req, event)
    filter_obj = AuditResultFilter(req, {"result": ["yes"]}, AuditLog, model_admin)
    assert list(filter_obj.queryset(req, rows).values_list("pk", flat=True)) == [sample_events.ok.pk]
