"""P01-A04: isolated test database only; never creates a live account."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from accounts.models import UserProfile
from auditlog.models import AuditLog
from tenants.models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from tenants.tests import core_tenant_tables
from tenants.user_services import (
    CompanyUserAuditUnavailable, CompanyUserEditConflict, EDIT_TOKEN_SALT,
    company_user_edit_state, update_company_user,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def records(core_tenant_tables):
    User = get_user_model()
    root = User.objects.create_superuser(username="a04_root", password="Test-Only!2026")
    user = User.objects.create_user(username="a04_target", password="Test-Only!2026",
                                   first_name="نام اولیه", email="before@example.test")
    core = CoreUser.objects.create(username=user.username, full_name=user.first_name,
                                   email=user.email, account_status="ACTIVE")
    user.profile.core_user_id = core.pk
    user.profile.must_change_password = True
    user.profile.save()
    first = Tenant.objects.create(tenant_code="A04_A", legal_name="A", display_name="الف")
    second = Tenant.objects.create(tenant_code="A04_B", legal_name="B", display_name="ب")
    operator = Role.objects.create(role_code="OPERATOR", role_name_fa="اپراتور")
    admin = Role.objects.create(role_code="COMPANY_ADMIN", role_name_fa="مدیر شرکت")
    global_role = Role.objects.create(role_code="SUPERADMIN", role_name_fa="سراسری")
    a = TenantMembership.objects.create(tenant=first, core_user=core)
    b = TenantMembership.objects.create(tenant=second, core_user=core, is_active=False)
    ra = UserRoleAssignment.objects.create(tenant=first, core_user=core, role=operator)
    rb = UserRoleAssignment.objects.create(tenant=second, core_user=core, role=admin)
    rg = UserRoleAssignment.objects.create(tenant=None, core_user=core, role=global_role)
    for name in ("OPERATOR", "UNRELATED_GROUP"):
        user.groups.add(Group.objects.create(name=name))
    return SimpleNamespace(root=root, user=user, core=core, first=first, second=second,
                           operator=operator, admin=admin, global_role=global_role,
                           a=a, b=b, ra=ra, rb=rb, rg=rg)


def state(r, tenant=None):
    return company_user_edit_state(actor=r.root, core_user=r.core, tenant=tenant or r.first)


def save(r, **changes):
    data = dict(actor=r.root, core_user=r.core, tenant=r.first, role=r.operator,
                full_name=r.core.full_name, email=r.core.email, account_status="ACTIVE",
                edit_token=changes.pop("edit_token") if "edit_token" in changes else state(r)["edit_token"])
    data.update(changes)
    return update_company_user(**data)


def snapshot(r):
    r.core.refresh_from_db(); r.user.refresh_from_db()
    return dict(core=list(CoreUser.objects.filter(pk=r.core.pk).values()),
                auth=list(get_user_model().objects.filter(pk=r.user.pk).values()),
                profile=list(UserProfile.objects.filter(user=r.user).values()),
                members=list(TenantMembership.objects.filter(core_user=r.core).order_by("pk").values()),
                roles=list(UserRoleAssignment.objects.filter(core_user=r.core).order_by("pk").values()),
                groups=list(r.user.groups.order_by("pk").values_list("pk", flat=True)))


def events(r):
    # Avoid counting unrelated middleware audit records from previous requests.
    return [event for event in AuditLog.objects.filter(entity_type="auth_user", entity_id=str(r.user.pk))
            if event.metadata.get("operation") == "company_user_update"]


def url(r, tenant=True):
    value = reverse("tenants:user_update", args=[r.core.pk])
    return value + (f"?tenant={r.first.pk}" if tenant else "")


def post_data(r, **changes):
    data = dict(full_name=r.core.full_name, email=r.core.email, tenant=r.first.pk,
                role=r.operator.pk, account_status="ACTIVE", edit_token=changes.pop("edit_token") if "edit_token" in changes else state(r)["edit_token"])
    data.update(changes)
    return data


def test_selected_role_updates_in_place_preserving_every_other_link(records):
    r = records; before = snapshot(r)
    _, changed = save(r, role=r.admin)
    after = snapshot(r)
    assert changed == ["company_role"]
    for key in ("core", "auth", "profile", "members", "groups"):
        assert after[key] == before[key]
    r.ra.refresh_from_db(); r.rb.refresh_from_db(); r.rg.refresh_from_db()
    assert r.ra.role_id == r.admin.pk
    assert after["roles"][1:] == before["roles"][1:]
    assert len(after["roles"]) == len(before["roles"])
    event, = events(r)
    assert event.tenant_id == r.first.pk and event.success
    assert event.metadata["role_assignment_id"] == r.ra.pk
    assert event.metadata["old_role_id"] == r.operator.pk
    assert event.metadata["new_role_id"] == r.admin.pk


def test_global_profile_changes_do_not_modify_credentials_or_memberships(records):
    r = records; before = snapshot(r)
    save(r, full_name="نام جدید", email="after@example.test", account_status="LOCKED")
    after = snapshot(r)
    assert r.core.full_name == r.user.first_name == "نام جدید"
    assert r.core.email == r.user.email == "after@example.test"
    assert r.core.account_status == "LOCKED" and not r.user.is_active
    for key in ("members", "roles", "groups", "profile"):
        assert after[key] == before[key]
    for key in ("username", "password", "is_staff", "is_superuser"):
        assert after["auth"][0][key] == before["auth"][0][key]
    serialized = json.dumps(events(r)[0].metadata, ensure_ascii=False)
    assert "after@example.test" not in serialized and "نام جدید" not in serialized


def test_inactive_membership_is_not_reactivated(records):
    r = records
    save(r, tenant=r.second, role=r.operator, edit_token=state(r, r.second)["edit_token"])
    r.b.refresh_from_db(); r.rb.refresh_from_db()
    assert not r.b.is_active and r.rb.role_id == r.operator.pk


def test_noop_does_not_write_timestamps_or_explicit_audit(records):
    r = records; before = snapshot(r); old_events = len(events(r))
    _, changed = save(r)
    assert changed == [] and snapshot(r) == before and len(events(r)) == old_events


@pytest.mark.parametrize("status", ["INACTIVE", "LOCKED"])
def test_root_cannot_be_disabled_here(records, status):
    r = records
    r.user.is_superuser = True; r.user.save(update_fields=["is_superuser"])
    before = snapshot(r)
    with pytest.raises(ValidationError): save(r, account_status=status)
    assert snapshot(r) == before


def test_existing_membership_without_role_can_receive_role_without_membership_creation(records):
    r = records; r.ra.delete(); before = snapshot(r)
    save(r, role=r.admin)
    after = snapshot(r)
    assert after["members"] == before["members"]
    assert UserRoleAssignment.objects.filter(core_user=r.core, tenant=r.first).get().role_id == r.admin.pk
    assert len(after["roles"]) == len(before["roles"]) + 1


def test_missing_membership_stops_without_creating_anything(records):
    r = records; token = state(r)["edit_token"]; r.ra.delete(); r.a.delete(); before = snapshot(r)
    with pytest.raises(ValidationError): save(r, edit_token=token)
    assert snapshot(r) == before


def test_missing_auth_mapping_stops_without_automatic_link(records):
    r = records; token = state(r)["edit_token"]
    UserProfile.objects.filter(user=r.user).update(core_user_id=None)
    before = snapshot(r)
    with pytest.raises(ValidationError):
        update_company_user(actor=r.root, core_user=r.core, tenant=r.first, role=r.admin,
                            full_name="تغییر", email="", account_status="ACTIVE", edit_token=token)
    assert snapshot(r) == before


def test_multiple_selected_company_roles_block_all_edits(records, client):
    r = records
    UserRoleAssignment.objects.create(core_user=r.core, tenant=r.first, role=r.admin)
    before = snapshot(r)
    with pytest.raises(ValidationError): state(r)
    client.force_login(r.root)
    assert client.get(url(r)).status_code == 400
    assert snapshot(r) == before


def test_multiple_roles_in_other_company_are_preserved(records):
    r = records
    UserRoleAssignment.objects.create(core_user=r.core, tenant=r.second, role=r.operator)
    before = list(UserRoleAssignment.objects.filter(tenant=r.second).values())
    save(r, role=r.admin)
    assert list(UserRoleAssignment.objects.filter(tenant=r.second).values()) == before


@pytest.mark.parametrize("change", ["name", "email", "status", "auth", "membership", "role", "new_role"])
def test_stale_form_rejects_global_and_selected_company_changes(records, change):
    r = records; token = state(r)["edit_token"]
    if change == "name": CoreUser.objects.filter(pk=r.core.pk).update(full_name="اپراتور دیگر")
    elif change == "email": CoreUser.objects.filter(pk=r.core.pk).update(email="other@example.test")
    elif change == "status": CoreUser.objects.filter(pk=r.core.pk).update(account_status="LOCKED")
    elif change == "auth": get_user_model().objects.filter(pk=r.user.pk).update(email="other@example.test")
    elif change == "membership": TenantMembership.objects.filter(pk=r.a.pk).update(is_active=False)
    elif change == "role": UserRoleAssignment.objects.filter(pk=r.ra.pk).update(role=r.admin)
    else: r.ra.delete()
    before = snapshot(r)
    with pytest.raises(CompanyUserEditConflict): save(r, edit_token=token, full_name="بازنویسی")
    assert snapshot(r) == before


def test_change_to_other_company_role_does_not_cause_loss_or_false_conflict(records):
    r = records; token = state(r)["edit_token"]
    UserRoleAssignment.objects.filter(pk=r.rb.pk).update(role=r.operator)
    save(r, edit_token=token, role=r.admin)
    r.rb.refresh_from_db()
    assert r.rb.role_id == r.operator.pk


@pytest.mark.parametrize("kind", ["tampered", "expired", "other_company", "other_user", "empty"])
def test_token_is_signed_expiring_and_bound_to_scope(records, kind):
    r = records; token = state(r)["edit_token"]
    if kind == "tampered": token += "x"
    elif kind == "empty": token = ""
    elif kind == "expired":
        with patch("django.core.signing.time.time", return_value=1):
            token = state(r)["edit_token"]
    else:
        data = signing.loads(token, salt=EDIT_TOKEN_SALT)
        data["tenant_id" if kind == "other_company" else "core_user_id"] = -1
        token = signing.dumps(data, salt=EDIT_TOKEN_SALT)
    before = snapshot(r)
    with pytest.raises(CompanyUserEditConflict): save(r, edit_token=token, role=r.admin)
    assert snapshot(r) == before


@pytest.mark.parametrize("kind", ["ordinary", "staff", "group_superadmin", "inactive_root"])
def test_service_requires_active_django_superuser(records, kind):
    r = records; token = state(r)["edit_token"]
    actor = get_user_model().objects.create_user(username="a04_denied_" + kind,
        is_staff=kind == "staff", is_superuser=kind == "inactive_root", is_active=kind != "inactive_root")
    if kind == "group_superadmin": actor.groups.add(Group.objects.create(name="SUPERADMIN"))
    before = snapshot(r)
    with pytest.raises(PermissionDenied): save(r, actor=actor, edit_token=token, role=r.admin)
    assert snapshot(r) == before


@pytest.mark.parametrize("failure", ["audit_none", "role_write"])
def test_failure_rolls_back_global_fields_and_selected_role(records, failure):
    r = records; before = snapshot(r)
    if failure == "audit_none":
        context = patch("tenants.user_services.record_audit_event", return_value=None)
        exception = CompanyUserAuditUnavailable
    else:
        context = patch.object(UserRoleAssignment, "save", side_effect=ValidationError("test failure"))
        exception = ValidationError
    with context, pytest.raises(exception):
        save(r, full_name="نباید ذخیره شود", role=r.admin, account_status="INACTIVE")
    assert snapshot(r) == before and not events(r)


def test_global_superadmin_role_cannot_be_assigned_by_form_service(records):
    r = records; before = snapshot(r)
    with pytest.raises(ValidationError): save(r, role=r.global_role)
    assert snapshot(r) == before


def test_read_only_get_requires_explicit_company_for_multi_membership(records, client):
    r = records; client.force_login(r.root); before = snapshot(r)
    response = client.get(url(r, tenant=False))
    assert response.status_code == 200
    assert response.context["memberships"] and "form" not in response.context
    assert f"?tenant={r.first.pk}" in response.content.decode()
    assert f"?tenant={r.second.pk}" in response.content.decode()
    assert snapshot(r) == before


def test_company_list_links_are_scoped(records, client):
    r = records; client.force_login(r.root)
    content = client.get(reverse("tenants:user_list")).content.decode()
    assert url(r) in content
    assert url(r, tenant=False) + f"?tenant={r.second.pk}" in content


def test_company_field_is_disabled_and_post_tamper_cannot_transfer_user(records, client):
    r = records; client.force_login(r.root)
    response = client.get(url(r))
    form = response.context["form"]
    assert form.fields["tenant"].disabled
    assert list(form.fields["tenant"].queryset.values_list("pk", flat=True)) == [r.first.pk]
    before = snapshot(r)
    response = client.post(url(r), post_data(r, tenant=r.second.pk, role=r.admin.pk))
    assert response.status_code == 302
    after = snapshot(r)
    assert after["members"] == before["members"] and after["roles"][1:] == before["roles"][1:]
    r.ra.refresh_from_db(); assert r.ra.role_id == r.admin.pk


def test_post_query_scope_change_is_rejected(records, client):
    r = records; client.force_login(r.root); before = snapshot(r)
    response = client.post(url(r, tenant=False) + f"?tenant={r.second.pk}", post_data(r, role=r.admin.pk))
    assert response.status_code == 409 and snapshot(r) == before


def test_stale_post_preserves_entered_text_but_disables_submit(records, client):
    r = records; client.force_login(r.root); data = post_data(r, full_name="متن واردشده")
    CoreUser.objects.filter(pk=r.core.pk).update(full_name="ذخیره اپراتور دیگر")
    before = snapshot(r)
    response = client.post(url(r), data)
    assert response.status_code == 409
    assert response.context["edit_blocked"] and response.context["form"]["full_name"].value() == data["full_name"]
    assert snapshot(r) == before


@pytest.mark.parametrize("company", ["0", "-1", "invalid", "999999999"])
def test_invalid_or_nonmember_scope_is_not_guessed(records, client, company):
    r = records; client.force_login(r.root); before = snapshot(r)
    assert client.get(url(r, tenant=False) + "?tenant=" + company).status_code == 404
    assert snapshot(r) == before


def test_empty_post_is_bound_and_cannot_write(records, client):
    r = records; client.force_login(r.root); before = snapshot(r)
    response = client.post(url(r), {})
    assert response.status_code == 400 and response.context["form"].is_bound
    assert snapshot(r) == before


def test_multi_company_post_without_scope_does_not_choose_first(records, client):
    r = records; client.force_login(r.root); before = snapshot(r)
    assert client.post(url(r, tenant=False), post_data(r)).status_code == 400
    assert snapshot(r) == before


def test_single_membership_legacy_link_is_unambiguous(records, client):
    r = records; r.rb.delete(); r.b.delete(); client.force_login(r.root)
    response = client.get(url(r, tenant=False))
    assert response.status_code == 200 and response.context["edit_tenant"].pk == r.first.pk


def test_inactive_membership_disclaimer_and_global_status_label(records, client):
    r = records; client.force_login(r.root)
    response = client.get(url(r, tenant=False) + f"?tenant={r.second.pk}")
    assert response.status_code == 200
    assert not response.context["membership_active"]
    content = response.content.decode()
    assert "عضویت این شرکت غیرفعال است" in content and "وضعیت کلی حساب ورود" in content


def test_csrf_and_method_protection(records):
    r = records; client = Client(enforce_csrf_checks=True); client.force_login(r.root)
    before = snapshot(r)
    assert client.post(url(r), post_data(r)).status_code == 403
    client.get(url(r))
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    assert client.put(url(r), {}, HTTP_X_CSRFTOKEN=token).status_code == 405
    assert snapshot(r) == before


def test_audit_failure_in_view_returns_error_without_partial_write(records, client):
    r = records; client.force_login(r.root); data = post_data(r, role=r.admin.pk); before = snapshot(r)
    with patch("tenants.user_services.record_audit_event", return_value=None):
        response = client.post(url(r), data)
    assert response.status_code == 400 and response.context["edit_blocked"]
    assert snapshot(r) == before and not events(r)


def test_reactivation_preserves_inactive_company_membership(records):
    r = records
    CoreUser.objects.filter(pk=r.core.pk).update(account_status="INACTIVE")
    get_user_model().objects.filter(pk=r.user.pk).update(is_active=False)
    r.core.refresh_from_db()
    before = snapshot(r)
    save(r, account_status="ACTIVE")
    after = snapshot(r)
    assert r.user.is_active and r.core.account_status == "ACTIVE"
    assert after["members"] == before["members"] and after["roles"] == before["roles"]
    assert not TenantMembership.objects.get(pk=r.b.pk).is_active


def test_global_django_groups_are_not_rebuilt_or_removed(records):
    r = records
    r.user.groups.add(Group.objects.create(name="SUPERADMIN"))
    before = snapshot(r)["groups"]
    save(r, role=r.admin)
    assert snapshot(r)["groups"] == before


def test_no_membership_get_and_post_never_create_account_or_membership(records, client):
    r = records
    UserRoleAssignment.objects.filter(core_user=r.core, tenant__isnull=False).delete()
    TenantMembership.objects.filter(core_user=r.core).delete()
    client.force_login(r.root)
    before = snapshot(r)
    assert client.get(url(r, tenant=False)).status_code == 200
    assert client.post(url(r, tenant=False), {"role": r.admin.pk}).status_code == 400
    assert snapshot(r) == before
