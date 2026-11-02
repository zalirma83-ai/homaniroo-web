from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory
from django.db import connection

from .middleware import AuditTrailMiddleware
from .models import AuditLog
from .services import audit_export_rows, build_audit_context, filter_audit_logs, record_audit_event, security_summary
from .signals import audit_login_failed


@pytest.fixture
def audit_core_tables(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS core")
        cursor.execute("""CREATE TABLE IF NOT EXISTS core.tenants (
            tenant_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_code TEXT NOT NULL UNIQUE,
            legal_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
    return True


def make_user(username="auditor"):
    return get_user_model().objects.create_user(username=username, password="Strong-Test-2026!")


@pytest.mark.django_db
def test_record_audit_event_keeps_actor_tenant_and_request(audit_core_tables):
    user = make_user()
    request = RequestFactory().post("/customers/1/update/")
    request.user = user
    request.tenant = SimpleNamespace(tenant_id=7)
    event = record_audit_event(action="CREATE_OR_UPDATE", request=request, entity_type="customer", entity_id="1", description="ویرایش مشتری")
    assert event.auth_user_id == user.pk
    assert event.tenant_id == 7
    assert event.username == "auditor"
    assert event.request_method == "POST"
    assert event.request_path == "/customers/1/update/"


@pytest.mark.django_db
def test_audit_filters_tenant_action_and_result(audit_core_tables):
    AuditLog.objects.create(tenant_id=1, action="LOGIN_SUCCESS", username="one", success=True)
    AuditLog.objects.create(tenant_id=2, action="LOGIN_FAILED", username="two", success=False, severity="IMPORTANT")
    result = filter_audit_logs(tenant_id="2", action="LOGIN_FAILED", success="no")
    assert list(result.values_list("username", flat=True)) == ["two"]


@pytest.mark.django_db
def test_audit_search_covers_description_entity_and_path(audit_core_tables):
    AuditLog.objects.create(action="SYSTEM", description="تغییر قرارداد", entity_type="contract", entity_id="88", request_path="/contracts/88/")
    assert filter_audit_logs(query="قرارداد").count() == 1
    assert filter_audit_logs(query="88").count() == 1


@pytest.mark.django_db
def test_middleware_records_authenticated_post_without_form_payload(audit_core_tables):
    request = RequestFactory().post("/companies/select/", {"tenant": "1", "password": "secret"})
    request.user = make_user("operator")
    request.tenant = SimpleNamespace(tenant_id=1)
    middleware = AuditTrailMiddleware(lambda req: HttpResponse("ok", status=200))
    middleware(request)
    event = AuditLog.objects.get()
    assert event.action == "SYSTEM"
    assert event.entity_type == "http_request"
    assert event.metadata["audit_scope"] == "http_request"
    assert event.metadata["business_outcome"] == "UNKNOWN"
    assert event.metadata["status_code"] == 200
    assert "password" not in str(event.metadata)
    assert "secret" not in str(event.metadata)


@pytest.mark.django_db
def test_middleware_does_not_record_get_requests(audit_core_tables):
    request = RequestFactory().get("/reports/")
    request.user = make_user("reader")
    request.tenant = SimpleNamespace(tenant_id=1)
    AuditTrailMiddleware(lambda req: HttpResponse("ok"))(request)
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_failed_login_signal_records_safe_security_event(audit_core_tables):
    request = RequestFactory().post("/account/login/")
    request.user = SimpleNamespace(is_authenticated=False)
    # Django's test client can emit auth signals with an unset request method.
    request.method = None
    audit_login_failed(sender=None, credentials={"username": "unknown", "password": "never-store"}, request=request)
    event = AuditLog.objects.get()
    assert event.action == "LOGIN_FAILED"
    assert event.success is False
    assert event.severity == "IMPORTANT"
    assert event.metadata == {"username": "unknown"}
    assert "never-store" not in str(event.metadata)


@pytest.mark.django_db
def test_security_summary_counts_users(audit_core_tables):
    make_user("active-user")
    inactive = make_user("inactive-user")
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    summary = security_summary()
    assert summary["users"]["total"] == 2
    assert summary["users"]["active"] == 1
    assert "axes" in summary


@pytest.mark.django_db
def test_audit_context_and_export_share_filters(audit_core_tables):
    AuditLog.objects.create(tenant_id=1, action="SYSTEM", username="first", description="رویداد اول")
    AuditLog.objects.create(tenant_id=2, action="SYSTEM", username="second", description="رویداد دوم")
    context = build_audit_context(tenant_id="1", query="", action="", severity="", success="", date_from="", date_to="")
    assert context["queryset"].count() == 1
    header, rows = audit_export_rows(tenant_id="1", query="", action="", severity="", success="", date_from="", date_to="")
    assert "کاربر" in header
    assert len(rows) == 1
    assert rows[0][1] == "first"
