"""Manual ownership is shared across screens, tenant-safe and never inferred."""
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from .models import EntityOwnership, EntityOwnershipChange, IndustrialUnit, TenantCustomer
from .ownership import annotate_ownership, attach_ownership, filter_customers, set_ownership
from .ownership_views import ownership_update
from .business_services import partners_for_tenant
from .test_business_profiles import business_tables, profile
from .tests import prospective_customer_tables, active_customer_tables, customer_profile_tables
from reports.tests import report_tables, make_contract
from interactions.models import FollowUp, Interaction, Alert
from tenants.tests import core_tenant_tables
from tenants.models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment


@pytest.fixture
def actor(core_tenant_tables):
    # A05: real company membership/role; a global group is no longer a grant.
    user = get_user_model().objects.create_user(username="ownership_operator")
    first = Tenant.objects.create(tenant_code="OWNERSHIP_A", legal_name="A", display_name="شرکت الف")
    second = Tenant.objects.create(tenant_code="OWNERSHIP_B", legal_name="B", display_name="شرکت ب")
    core = CoreUser.objects.create(full_name="اپراتور مالکیت", account_status="ACTIVE")
    user.profile.core_user_id = core.pk
    user.profile.save(update_fields=["core_user_id"])
    role = Role.objects.create(role_code="OPERATOR", role_name_fa="اپراتور")
    TenantMembership.objects.create(tenant=first, core_user=core, is_active=True)
    UserRoleAssignment.objects.create(tenant=first, core_user=core, role=role)
    user._ownership_tenants = (first, second)
    return user


@pytest.fixture
def records(business_tables, actor):
    tenant, other_tenant = actor._ownership_tenants
    item = IndustrialUnit.objects.create(official_name="واحد مشترک", normalized_name="shared", ownership_type="PRIVATE")
    first = TenantCustomer.objects.create(tenant_id=tenant.pk, industrial_unit=item, customer_status="ACTIVE")
    second = TenantCustomer.objects.create(tenant_id=other_tenant.pk, industrial_unit=item, customer_status="LEAD")
    return N(unit=item, first=first, second=second, tenant=tenant, other_tenant=other_tenant)


def req(user, unit_id, data=None, tenant=None, method="post"):
    path = reverse("customers:ownership_update", args=[unit_id])
    request = getattr(RequestFactory(), method)(path, data or {})
    request.tenant = N(tenant_id=tenant) if tenant is not None else getattr(user, "_ownership_tenants", (N(tenant_id=1),))[0]
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_default_is_unknown_even_if_legacy_import_has_private(records):
    row = filter_customers(TenantCustomer.objects.all(), records.tenant).get()
    assert row.ownership_code == "UNKNOWN"
    assert not EntityOwnership.objects.exists()
    assert not EntityOwnershipChange.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["PUBLIC", "PRIVATE", "UNKNOWN"])
def test_operator_choice_is_persisted_without_changing_core_or_activity(records, actor, code):
    set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value=code, user=actor)
    records.unit.refresh_from_db();records.first.refresh_from_db();records.second.refresh_from_db()
    assert records.unit.ownership_type == "PRIVATE"  # imported legacy data remains untouched
    assert records.first.customer_status == "ACTIVE" and records.second.customer_status == "LEAD"
    assert filter_customers(TenantCustomer.objects.all(), records.tenant).get().ownership_code == code
    assert filter_customers(TenantCustomer.objects.all(), records.other_tenant).get().ownership_code == "UNKNOWN"


@pytest.mark.django_db
def test_reset_and_repeat_are_audited_only_for_real_changes(records, actor):
    for code in ["PUBLIC", "PUBLIC", "PRIVATE", "UNKNOWN"]:
        set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value=code, user=actor)
    changes = list(EntityOwnershipChange.objects.for_tenant(records.tenant).order_by("id"))
    assert [(row.previous_type, row.new_type) for row in changes] == [("UNKNOWN", "PUBLIC"), ("PUBLIC", "PRIVATE"), ("PRIVATE", "UNKNOWN")]
    assert all(row.changed_by_user_id == actor.pk and row.changed_by_name == actor.username for row in changes)


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["", "MIXED", "public", "<script>", None])
def test_invalid_values_do_not_write(records, actor, invalid):
    with pytest.raises(ValidationError):
        set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value=invalid, user=actor)
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_foreign_and_nonmember_unit_cannot_be_changed(records, actor):
    foreign = IndustrialUnit.objects.create(official_name="شرکت دیگر", normalized_name="foreign")
    TenantCustomer.objects.create(tenant_id=records.other_tenant.pk, industrial_unit=foreign)
    with pytest.raises(Http404):
        set_ownership(tenant=records.tenant, unit_id=foreign.pk, value="PUBLIC", user=actor)
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_no_active_tenant_is_not_an_edit_permission(records, actor):
    with pytest.raises(PermissionDenied):
        set_ownership(tenant=None, unit_id=records.unit.pk, value="PUBLIC", user=actor)
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_unprivileged_user_rejected_in_view_and_service(records):
    user = get_user_model().objects.create_user(username="reader")
    with pytest.raises(PermissionDenied):
        set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PRIVATE", user=user)
    with pytest.raises(PermissionDenied):
        ownership_update(req(user, records.unit.pk, {"ownership_type": "PUBLIC"}), records.unit.pk)
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_get_is_rejected_and_csrf_is_required(records, actor):
    assert ownership_update(req(actor, records.unit.pk, method="get"), records.unit.pk).status_code == 405
    request = req(actor, records.unit.pk, {"ownership_type": "PUBLIC"})
    assert CsrfViewMiddleware(lambda r: None).process_view(request, ownership_update, (), {}).status_code == 403
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_invalid_form_and_unsafe_redirect_are_safe(records, actor):
    response = ownership_update(req(actor, records.unit.pk, {"ownership_type": "invalid", "next": "https://evil.invalid/"}), records.unit.pk)
    assert response.status_code == 400
    assert not EntityOwnership.objects.exists()
    response = ownership_update(req(actor, records.unit.pk, {"ownership_type": "PUBLIC", "next": "https://evil.invalid/"}), records.unit.pk)
    assert response.status_code == 302
    assert response.url == reverse("customers:partner_list")


@pytest.mark.django_db
def test_audit_failure_rolls_back_ownership(records, actor):
    with patch.object(EntityOwnershipChange.objects, "create", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PUBLIC", user=actor)
    assert not EntityOwnership.objects.exists()


@pytest.mark.django_db
def test_model_enforces_valid_choices_and_unique_tenant_unit(records):
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityOwnership.objects.create(tenant_id=records.tenant.pk, industrial_unit_id=records.unit.pk, ownership_type="OTHER")
    EntityOwnership.objects.create(tenant_id=records.tenant.pk, industrial_unit_id=records.unit.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityOwnership.objects.create(tenant_id=records.tenant.pk, industrial_unit_id=records.unit.pk)


@pytest.mark.django_db
def test_partner_without_customer_membership_uses_the_same_source(business_tables, actor):
    records = N(tenant=actor._ownership_tenants[0])
    entry = profile(tenant_id=records.tenant.pk)
    set_ownership(tenant=records.tenant, unit_id=entry.industrial_unit_id, value="PUBLIC", user=actor)
    assert partners_for_tenant(records.tenant).get().ownership_code == "PUBLIC"
    assert not TenantCustomer.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["ACTIVE", "LEAD", "DORMANT"])
def test_filter_and_detail_attachment_are_consistent_for_every_status(records, actor, status):
    records.first.customer_status = status; records.first.save()
    set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PRIVATE", user=actor)
    qs = TenantCustomer.objects.for_tenant(records.tenant)
    assert filter_customers(qs, records.tenant, "PRIVATE").get().pk == records.first.pk
    assert not filter_customers(qs, records.tenant, "PUBLIC").exists()
    attach_ownership([records.first], records.tenant)
    assert records.first.ownership_label == "خصوصی"
    assert records.first.ownership.changed_by_user_id == actor.pk


@pytest.mark.django_db
def test_identity_copy_does_not_lose_operator_choice(records, actor):
    from .services import update_prospective_customer
    records.first.customer_status = "LEAD";records.first.save()
    set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PUBLIC", user=actor)
    previous = EntityOwnership.objects.get(tenant_id=records.tenant.pk, industrial_unit_id=records.unit.pk)
    updated = update_prospective_customer(tenant=records.tenant, customer=records.first, cleaned_data={"official_name": "نام ویرایش شده"})
    assert updated.industrial_unit_id != records.unit.pk
    attach_ownership([updated], records.tenant)
    assert updated.ownership_code == "PUBLIC"
    assert updated.ownership.changed_at == previous.changed_at
    assert not EntityOwnership.objects.for_tenant(records.other_tenant).exists()


@pytest.mark.django_db
def test_contract_report_csv_and_activity_observe_the_same_choice(records, actor, report_tables):
    from contracts.services import filter_contracts, contracts_for_tenant, enrich_contracts
    from reports.services import build_management_report, export_rows
    contract = make_contract(records.first, "OWNERSHIP")
    set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PUBLIC", user=actor)
    qs = filter_contracts(contracts_for_tenant(records.tenant), records.tenant, ownership="PUBLIC")
    assert qs.get().pk == contract.pk
    assert enrich_contracts(records.tenant, qs)[0]["customer"].ownership_code == "PUBLIC"
    assert not filter_contracts(contracts_for_tenant(records.tenant), records.tenant, ownership="PRIVATE").exists()
    assert build_management_report(records.tenant, ownership="PUBLIC")["customers"]["total"] == 1
    assert build_management_report(records.tenant, ownership="PRIVATE")["customers"]["total"] == 0
    for key in ["customers", "contracts"]:
        header, rows = export_rows(records.tenant, key, ownership="PUBLIC")
        assert len(rows) == 1 and rows[0][header.index("نوع مالکیت")] == "دولتی"
        assert not export_rows(records.tenant, key, ownership="PRIVATE")[1]
    with pytest.raises(ValueError):
        export_rows(records.tenant, "energy", ownership="PUBLIC")


@pytest.mark.django_db
def test_queue_alerts_and_activity_csv_filter_by_the_same_source(records, actor, report_tables):
    from interactions.services import build_work_queue
    from interactions.alert_services import build_alert_center
    from reports.services import export_rows
    set_ownership(tenant=records.tenant, unit_id=records.unit.pk, value="PUBLIC", user=actor)
    now = timezone.now()
    FollowUp.objects.create(tenant_id=records.tenant.pk, tenant_customer_id=records.first.pk, due_at=now, follow_up_status="OPEN")
    Interaction.objects.create(tenant_id=records.tenant.pk, tenant_customer_id=records.first.pk, interaction_type="CALL", source_system="TEST", interaction_at=now)
    Alert.objects.create(tenant_id=records.tenant.pk, tenant_customer_id=records.first.pk, alert_type="FOLLOW_UP", severity="NORMAL", alert_status="OPEN", message="هشدار")
    assert len(build_work_queue(records.tenant, ownership="PUBLIC")["rows"]) == 1
    assert len(build_work_queue(records.tenant, ownership="PUBLIC")["recent_rows"]) == 1
    assert not build_work_queue(records.tenant, ownership="PRIVATE")["recent_rows"]
    assert build_alert_center(records.tenant, ownership="PUBLIC")["rows"][0]["customer"].ownership_code == "PUBLIC"
    assert not build_alert_center(records.tenant, ownership="PRIVATE")["rows"]
    for key in ["interactions", "followups", "alerts"]:
        header, rows = export_rows(records.tenant, key, ownership="PUBLIC")
        assert len(rows) == 1 and rows[0][header.index("نوع مالکیت")] == "دولتی"
        assert not export_rows(records.tenant, key, ownership="PRIVATE")[1]


def test_display_partial_escapes_unknown_input_and_has_three_choices():
    html = render_to_string("partials/ownership_badge.html", {"code": "<script>"})
    assert "نامشخص" in html and "<script>" not in html
    html = render_to_string("partials/ownership_filter.html", {"ownership_filter": "PRIVATE"})
    for code in ["UNKNOWN", "PUBLIC", "PRIVATE"]:
        assert 'value="' + code + '"' in html
    assert 'value="PRIVATE" selected' in html


def test_base_stylesheet_tag_is_valid():
    from pathlib import Path
    from django.conf import settings
    source = (Path(settings.BASE_DIR) / "templates/base.html").read_text(encoding="utf-8")
    assert '<<link' not in source
    assert '<link rel="stylesheet" href="{% static \'css/app.css\' %}?v=417">' in source
