"""E02: real DB integration tests; PostgreSQL fixtures match the existing suite.
No live account or live database is used by these tests.
"""
import json
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse

from auditlog.models import AuditLog
from tenants.models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from tenants.tests import core_tenant_tables
from tenants.user_services import create_company_user
from .admin_site import SuperadminAuthenticationForm
from .credential_services import CredentialAuditUnavailable, set_user_credentials
from .forms import CredentialManagementForm

pytestmark = pytest.mark.django_db
OLD_PASSWORD = "Old-Secure-Credential!2026"
NEW_PASSWORD = "New-Secure-Credential!2026"


@pytest.fixture(autouse=True)
def credential_tables(core_tenant_tables):
    return core_tenant_tables


@pytest.fixture
def root():
    return get_user_model().objects.create_superuser(username="credential_root", password=OLD_PASSWORD)


@pytest.fixture
def target():
    user = get_user_model().objects.create_user(username="credential_target", password=OLD_PASSWORD)
    core = CoreUser.objects.create(username=user.username, full_name="کاربر آزمایشی", account_status="ACTIVE")
    user.profile.core_user_id = core.pk
    user.profile.must_change_password = True
    user.profile.save()
    return user


@pytest.fixture
def company_links(target):
    first = Tenant.objects.create(tenant_code="E02_FIRST", legal_name="First", display_name="اول")
    second = Tenant.objects.create(tenant_code="E02_SECOND", legal_name="Second", display_name="دوم")
    role = Role.objects.create(role_code="OPERATOR", role_name_fa="اپراتور")
    for tenant in (first, second):
        TenantMembership.objects.create(tenant=tenant, core_user_id=target.profile.core_user_id)
        UserRoleAssignment.objects.create(tenant=tenant, core_user_id=target.profile.core_user_id, role=role)
    target.groups.add(Group.objects.create(name="OPERATOR"))
    return first, second, role


def credentials_url(user):
    return reverse("accounts:user_credentials", args=[user.pk])


def post_credentials(client, user, username=None, password=NEW_PASSWORD, **extra):
    data = {"username": username or user.username, "new_password1": password, "new_password2": password}
    data.update(extra)
    return client.post(credentials_url(user), data)


@pytest.mark.parametrize("kind", ["operator", "company_admin", "staff", "group_superadmin"])
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("route", ["self", "done", "list", "target", "company_list", "create", "edit"])
def test_non_superuser_cannot_access_credential_or_user_management(client, target, kind, method, route):
    user = get_user_model().objects.create_user(username="denied_" + kind, password=OLD_PASSWORD, is_staff=kind == "staff")
    if kind in {"company_admin", "group_superadmin"}:
        user.groups.add(Group.objects.create(name="COMPANY_ADMIN" if kind == "company_admin" else "SUPERADMIN"))
    if kind == "staff":
        user.user_permissions.set(Permission.objects.all())
    urls = {
        "self": reverse("accounts:password_change"), "done": reverse("accounts:password_change_done"),
        "list": reverse("accounts:credential_user_list"), "target": credentials_url(target),
        "company_list": reverse("tenants:user_list"), "create": reverse("tenants:user_create"),
        "edit": reverse("tenants:user_update", args=[target.profile.core_user_id]),
    }
    original = target.password
    client.force_login(user)
    response = getattr(client, method)(urls[route], {"username": "hacked", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert response.status_code == 403
    target.refresh_from_db()
    assert target.username == "credential_target"
    assert target.password == original


def test_anonymous_user_redirects_to_login(client, target):
    response = client.get(credentials_url(target))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_superuser_can_open_all_accounts_even_without_core_profile_link(client, root, target):
    client.force_login(root)
    response = client.get(reverse("accounts:credential_user_list"))
    assert response.status_code == 200
    assert root.username in response.content.decode()
    assert target.username in response.content.decode()
    assert client.get(reverse("accounts:password_change")).status_code == 200


def test_get_does_not_reveal_password_or_hash(client, root, target):
    client.force_login(root)
    response = client.get(credentials_url(target))
    html = response.content.decode()
    assert response.status_code == 200
    assert OLD_PASSWORD not in html
    assert target.password not in html
    assert 'value="' + NEW_PASSWORD not in html
    assert "old_password" not in html
    assert "no-store" in response["Cache-Control"]


def test_reset_password_and_username_syncs_core_and_audits_without_secrets(client, root, target, company_links):
    memberships = list(TenantMembership.objects.values_list("tenant_user_id", "tenant_id", "is_active"))
    assignments = list(UserRoleAssignment.objects.values_list("user_role_assignment_id", "tenant_id", "role_id"))
    groups = list(target.groups.values_list("pk", flat=True))
    old_hash = target.password
    client.force_login(root)
    response = post_credentials(client, target, username="Renamed.Operator")
    assert response.status_code == 302
    target.refresh_from_db()
    assert target.username == "renamed.operator"
    assert target.check_password(NEW_PASSWORD)
    assert not target.check_password(OLD_PASSWORD)
    assert CoreUser.objects.get(pk=target.profile.core_user_id).username == target.username
    target.profile.refresh_from_db()
    assert target.profile.must_change_password is False
    assert list(TenantMembership.objects.values_list("tenant_user_id", "tenant_id", "is_active")) == memberships
    assert list(UserRoleAssignment.objects.values_list("user_role_assignment_id", "tenant_id", "role_id")) == assignments
    assert list(target.groups.values_list("pk", flat=True)) == groups
    event = AuditLog.objects.get(entity_type="auth_user", entity_id=str(target.pk), metadata__operation="credentials_update", success=True)
    assert event.auth_user_id == root.pk
    assert {"username", "password"}.issubset(event.metadata["changed_fields"])
    payload = json.dumps(list(AuditLog.objects.values()), default=str)
    for secret in (OLD_PASSWORD, NEW_PASSWORD, old_hash, target.password):
        assert secret not in payload


def test_username_only_does_not_change_password(client, root, target):
    original = target.password
    client.force_login(root)
    assert post_credentials(client, target, username="new_name", password="").status_code == 302
    target.refresh_from_db()
    assert target.password == original
    assert target.username == "new_name"


@pytest.mark.parametrize("case", ["weak", "mismatch", "duplicate_auth", "duplicate_core", "invalid_username"])
def test_invalid_credentials_do_not_change_anything_or_echo_password(client, root, target, case):
    original = target.password
    username = target.username
    password = NEW_PASSWORD
    extra = {}
    if case == "weak":
        password = "123"
    elif case == "mismatch":
        extra["new_password2"] = "Different-Secret!2026"
    elif case == "duplicate_auth":
        username = root.username.upper()
    elif case == "duplicate_core":
        CoreUser.objects.create(username="reserved_name", full_name="رزرو")
        username = "RESERVED_NAME"
    else:
        username = "invalid name"
    client.force_login(root)
    response = post_credentials(client, target, username=username, password=password, **extra)
    assert response.status_code == 400
    target.refresh_from_db()
    assert target.password == original
    assert target.username == "credential_target"
    assert NEW_PASSWORD not in response.content.decode()
    assert not AuditLog.objects.filter(entity_id=str(target.pk), metadata__operation="credentials_update", success=True).exists()
    assert AuditLog.objects.filter(entity_id=str(target.pk), metadata__operation="credentials_update", success=False).exists()


def test_credential_change_rolls_back_if_audit_cannot_be_written(client, root, target):
    original = target.password
    client.force_login(root)
    with patch("accounts.credential_services.record_audit_event", return_value=None):
        response = post_credentials(client, target, username="must_rollback")
    assert response.status_code == 503
    target.refresh_from_db()
    target.profile.refresh_from_db()
    assert target.username == "credential_target"
    assert target.password == original
    assert target.profile.must_change_password is True
    assert CoreUser.objects.get(pk=target.profile.core_user_id).username == target.username


def test_service_also_rejects_non_superuser(target):
    with pytest.raises(PermissionDenied):
        set_user_credentials(actor=target, target_id=target.pk, username="not_allowed", new_password=NEW_PASSWORD)
    target.refresh_from_db()
    assert target.check_password(OLD_PASSWORD)


def test_service_validates_password_independently_of_form(root, target):
    with pytest.raises(ValidationError):
        set_user_credentials(actor=root, target_id=target.pk, username=target.username, new_password="123")
    target.refresh_from_db()
    assert target.check_password(OLD_PASSWORD)


def test_superadmin_self_change_keeps_current_session(client, root):
    client.force_login(root)
    response = client.post(reverse("accounts:password_change"), {
        "username": "renamed_root", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD,
    })
    assert response.status_code == 302
    root.refresh_from_db()
    assert root.username == "renamed_root"
    assert root.check_password(NEW_PASSWORD)
    assert client.session[SESSION_KEY] == str(root.pk)
    assert client.get(reverse("accounts:credential_user_list")).status_code == 200


def test_password_reset_invalidates_other_users_existing_session(client, root, target, django_user_model):
    from django.test import Client
    other = Client()
    other.force_login(target)
    client.force_login(root)
    assert post_credentials(client, target).status_code == 302
    response = other.get(reverse("accounts:sessions"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_reset_user_can_login_with_new_password_only(client, root, target):
    client.force_login(root)
    assert post_credentials(client, target).status_code == 302
    client.logout()
    response = client.post(reverse("accounts:login"), {"username": target.username, "password": NEW_PASSWORD})
    assert response.status_code == 302
    assert client.session[SESSION_KEY] == str(target.pk)
    client.logout()
    response = client.post(reverse("accounts:login"), {"username": target.username, "password": OLD_PASSWORD})
    assert response.status_code == 200
    assert SESSION_KEY not in client.session


def test_operator_with_legacy_must_change_flag_is_not_forced_to_password_page(client, target):
    assert target.profile.must_change_password is True
    response = client.post(reverse("accounts:login"), {"username": target.username, "password": OLD_PASSWORD})
    assert response.status_code == 302
    assert response.url == reverse("dashboard:home")
    assert client.get(reverse("accounts:sessions")).status_code == 200
    assert client.get(reverse("accounts:password_change")).status_code == 403


def test_new_operator_has_no_forced_password_change(company_links):
    first, _, role = company_links
    user, core_user = create_company_user(username="created_operator", full_name="اپراتور", email="", password=NEW_PASSWORD, tenant=first, role=role)
    assert user.profile.must_change_password is False
    assert user.check_password(NEW_PASSWORD)


def test_create_account_is_superadmin_only_and_audited(client, root, company_links):
    first, _, role = company_links
    client.force_login(root)
    response = client.post(reverse("tenants:user_create"), {
        "username": "created_by_root", "full_name": "اپراتور جدید", "email": "",
        "tenant": first.pk, "role": role.pk, "password1": NEW_PASSWORD, "password2": NEW_PASSWORD,
    })
    assert response.status_code == 302
    user = get_user_model().objects.get(username="created_by_root")
    assert not user.profile.must_change_password
    assert AuditLog.objects.filter(entity_id=str(user.pk), metadata__operation="account_create", auth_user_id=root.pk).exists()


def test_create_account_rolls_back_if_audit_fails(client, root, company_links):
    first, _, role = company_links
    client.force_login(root)
    with patch("tenants.views.record_audit_event", return_value=None):
        response = client.post(reverse("tenants:user_create"), {
            "username": "never_created", "full_name": "عدم ثبت", "tenant": first.pk, "role": role.pk,
            "password1": NEW_PASSWORD, "password2": NEW_PASSWORD,
        })
    assert response.status_code == 400
    assert not get_user_model().objects.filter(username="never_created").exists()
    assert not CoreUser.objects.filter(username="never_created").exists()


def test_non_superuser_staff_cannot_use_django_admin_with_all_permissions(client, target):
    target.is_staff = True
    target.save(update_fields=["is_staff"])
    target.user_permissions.set(Permission.objects.all())
    client.force_login(target)
    for url in (reverse("admin:index"), reverse("admin:password_change"),
                reverse("admin:auth_user_change", args=[target.pk]),
                reverse("admin:auth_user_password_change", args=[target.pk])):
        response = client.post(url, {"username": "admin_bypass", "password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
        assert response.status_code in (302, 403)
        if response.status_code == 302:
            assert reverse("admin:login") in response.url
    target.refresh_from_db()
    assert target.username == "credential_target"
    assert target.check_password(OLD_PASSWORD)


def test_django_admin_login_rejects_staff_without_superuser(target):
    target.is_staff = True
    with pytest.raises(ValidationError):
        SuperadminAuthenticationForm().confirm_login_allowed(target)


def test_django_admin_password_routes_use_central_service(client, root, target):
    client.force_login(root)
    response = client.get(reverse("admin:password_change"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:password_change")
    response = client.get(reverse("admin:auth_user_password_change", args=[target.pk]))
    assert response.status_code == 302
    assert response.url == credentials_url(target)


def test_django_admin_user_form_does_not_expose_hash_or_allow_username_bypass(client, root, target):
    client.force_login(root)
    response = client.get(reverse("admin:auth_user_change", args=[target.pk]))
    assert response.status_code == 200
    assert target.password not in response.content.decode()
    form = response.context["adminform"].form
    assert "username" not in form.fields
    assert "password" not in form.fields


def test_credential_links_hidden_from_operators(client, target):
    client.force_login(target)
    html = client.get(reverse("accounts:sessions")).content.decode()
    assert reverse("accounts:password_change") not in html
    assert reverse("accounts:credential_user_list") not in html
    assert reverse("admin:index") not in html


def test_non_post_methods_cannot_modify_credentials(client, root, target):
    client.force_login(root)
    original = target.password
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(credentials_url(target)).status_code == 405
    target.refresh_from_db()
    assert target.password == original


def test_csrf_is_still_required(root, target):
    from django.test import Client
    client = Client(enforce_csrf_checks=True)
    client.force_login(root)
    assert post_credentials(client, target).status_code == 403
    target.refresh_from_db()
    assert target.check_password(OLD_PASSWORD)


def test_password_fields_never_render_bound_values(target):
    form = CredentialManagementForm({"username": target.username, "new_password1": NEW_PASSWORD, "new_password2": "different"}, target_user=target)
    assert not form.is_valid()
    assert NEW_PASSWORD not in form.as_p()


def test_linked_superadmin_can_rename_self_without_losing_global_role(client, root):
    core = CoreUser.objects.create(username=root.username, full_name="سوپرادمین", account_status="ACTIVE")
    role = Role.objects.create(role_code="SUPERADMIN", role_name_fa="سوپرادمین")
    assignment = UserRoleAssignment.objects.create(core_user=core, role=role, tenant=None)
    root.profile.core_user_id = core.pk
    root.profile.save()
    root.groups.add(Group.objects.create(name="SUPERADMIN"))
    client.force_login(root)
    assert post_credentials(client, root, username="root_new").status_code == 302
    root.refresh_from_db()
    core.refresh_from_db()
    assert root.username == core.username == "root_new"
    assert root.is_superuser and root.is_staff and root.is_active
    assert root.groups.filter(name="SUPERADMIN").exists()
    assert UserRoleAssignment.objects.filter(pk=assignment.pk, tenant=None, role=role).exists()
    assert client.get(reverse("accounts:credential_user_list")).status_code == 200


def test_noop_does_not_write_a_sensitive_change_event(client, root):
    client.force_login(root)
    response = post_credentials(client, root, password="")
    assert response.status_code == 302
    assert not AuditLog.objects.filter(metadata__operation="credentials_update", success=True).exists()
