"""P03-A03: safe, company-scoped operator management."""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.models import UserProfile
from accounts.test_p01_tenant_access import access_records
from auditlog.models import AuditLog
from tenants.models import CoreUser, TenantMembership, UserRoleAssignment
from tenants.operator_services import (OperatorAuditUnavailable, OperatorConflict, create_operator,
                                       operator_edit_state, update_operator)
from tenants.tests import core_tenant_tables

pytestmark = pytest.mark.django_db
PASSWORD = 'Sample-Only-Strong-4067!'
NEW_PASSWORD = 'Another-Only-Strong-5913!'


@pytest.fixture
def manager(access_records):
    r = access_records
    r.assignment.role = r.roles['COMPANY_ADMIN']
    r.assignment.save(update_fields=['role'])
    return r


def new_operator(r, username='p03_new'):
    return create_operator(actor=r.user, tenant=r.first, username=username,
                           full_name='اپراتور تست', email='', password=PASSWORD)[0]


def edits(r, user, **kwargs):
    state = operator_edit_state(actor=r.user, tenant=r.first, user_id=user.pk)
    data = dict(actor=r.user, tenant=r.first, user_id=user.pk, full_name=state['core'].full_name,
                username=user.username, account_status=state['core'].account_status,
                new_password='', edit_token=state['token'])
    data.update(kwargs)
    return data


def test_create_only_ordinary_scoped_operator_and_audit(manager):
    r = manager
    u = new_operator(r)
    assert u.check_password(PASSWORD) and not u.is_staff and not u.is_superuser
    core = CoreUser.objects.get(pk=u.profile.core_user_id)
    assert core.username == u.username and core.account_status == 'ACTIVE'
    assert list(TenantMembership.objects.filter(core_user=core).values_list('tenant_id', flat=True)) == [r.first.pk]
    assert list(UserRoleAssignment.objects.filter(core_user=core).values_list('tenant_id', 'role__role_code')) == [(r.first.pk, 'OPERATOR')]
    event = AuditLog.objects.get(entity_id=str(u.pk), metadata__operation='company_operator_create')
    assert event.tenant_id == r.first.pk and event.success
    assert PASSWORD not in str(event.metadata) and PASSWORD not in event.description


def test_create_rolls_back_all_if_audit_unavailable(manager):
    r = manager
    with patch('tenants.operator_services.record_audit_event', return_value=None):
        with pytest.raises(OperatorAuditUnavailable): new_operator(r, 'p03_rollback')
    assert not get_user_model().objects.filter(username='p03_rollback').exists()
    assert not CoreUser.objects.filter(username='p03_rollback').exists()


def test_create_cannot_grant_privileged_group_permissions(manager):
    group = Group.objects.create(name='OPERATOR')
    group.permissions.add(Permission.objects.first())
    with pytest.raises(Exception) as error:
        new_operator(manager, 'p03_privileged_group')
    from django.core.exceptions import ValidationError
    assert isinstance(error.value, ValidationError)
    assert not get_user_model().objects.filter(username='p03_privileged_group').exists()


def test_update_username_password_deactivate_and_reactivate(manager):
    r = manager; u = new_operator(r)
    old_hash = u.password
    _, changed = update_operator(**edits(r, u, full_name='نام جدید', username='new.operator',
                                      account_status='INACTIVE', new_password=NEW_PASSWORD))
    u.refresh_from_db(); core = CoreUser.objects.get(pk=u.profile.core_user_id)
    assert u.username == core.username == 'new.operator'
    assert core.full_name == 'نام جدید' and not u.is_active and core.account_status == 'INACTIVE'
    assert u.password != old_hash and u.check_password(NEW_PASSWORD) and 'password' in changed
    assert TenantMembership.objects.filter(core_user=core, tenant=r.first, is_active=True).exists()
    event = AuditLog.objects.filter(entity_id=str(u.pk), metadata__operation='company_operator_update').latest('pk')
    assert NEW_PASSWORD not in str(event.metadata) and NEW_PASSWORD not in event.description
    update_operator(**edits(r, u, account_status='ACTIVE'))
    u.refresh_from_db(); core.refresh_from_db()
    assert u.is_active and core.account_status == 'ACTIVE' and u.check_password(NEW_PASSWORD)


def test_edit_rolls_back_everything_on_audit_failure(manager):
    r = manager; u = new_operator(r); old_hash = u.password
    with patch('tenants.operator_services.record_audit_event', return_value=None):
        with pytest.raises(OperatorAuditUnavailable):
            update_operator(**edits(r, u, username='rollback_name', account_status='INACTIVE', new_password=NEW_PASSWORD))
    u.refresh_from_db(); core = CoreUser.objects.get(pk=u.profile.core_user_id)
    assert u.username == core.username == 'p03_new' and u.is_active and core.account_status == 'ACTIVE'
    assert u.password == old_hash and u.check_password(PASSWORD)


def test_stale_or_cross_scope_token_never_updates(manager):
    r = manager; u = new_operator(r)
    payload = edits(r, u, username='never_written')
    u.set_password('Externally-Changed-3419!'); u.save(update_fields=['password'])
    with pytest.raises(OperatorConflict): update_operator(**payload)
    u.refresh_from_db(); assert u.username == 'p03_new'
    payload = edits(r, u, edit_token='invalid')
    with pytest.raises(OperatorConflict): update_operator(**payload)


@pytest.mark.parametrize('case', ['other_tenant', 'multiple_tenants', 'inactive_second_membership',
    'company_admin', 'additional_role', 'global_role', 'superuser', 'staff', 'other_group',
    'direct_permission', 'privileged_operator_group', 'username_mismatch', 'status_mismatch', 'locked'])
def test_manager_cannot_manage_ambiguous_or_privileged_account(manager, case):
    r = manager; u = new_operator(r); core = CoreUser.objects.get(pk=u.profile.core_user_id)
    membership = TenantMembership.objects.get(core_user=core)
    assignment = UserRoleAssignment.objects.get(core_user=core)
    if case == 'other_tenant': membership.tenant = r.second; membership.save(update_fields=['tenant'])
    elif case in ('multiple_tenants', 'inactive_second_membership'):
        TenantMembership.objects.create(tenant=r.second, core_user=core, is_active=case == 'multiple_tenants')
    elif case == 'company_admin': assignment.role = r.roles['COMPANY_ADMIN']; assignment.save(update_fields=['role'])
    elif case == 'additional_role': UserRoleAssignment.objects.create(core_user=core, tenant=r.first, role=r.roles['SENIOR_OPERATOR'])
    elif case == 'global_role': UserRoleAssignment.objects.create(core_user=core, tenant=None, role=r.roles['COMPANY_ADMIN'])
    elif case == 'superuser': u.is_superuser = True; u.save(update_fields=['is_superuser'])
    elif case == 'staff': u.is_staff = True; u.save(update_fields=['is_staff'])
    elif case == 'other_group': u.groups.add(Group.objects.create(name='OTHER_PRIVILEGE'))
    elif case == 'direct_permission': u.user_permissions.add(Permission.objects.first())
    elif case == 'privileged_operator_group': Group.objects.get(name='OPERATOR').permissions.add(Permission.objects.first())
    elif case == 'username_mismatch': core.username = 'different'; core.save(update_fields=['username'])
    elif case == 'status_mismatch': core.account_status = 'INACTIVE'; core.save(update_fields=['account_status'])
    elif case == 'locked': core.account_status = 'LOCKED'; core.save(update_fields=['account_status'])
    with pytest.raises(PermissionDenied):
        operator_edit_state(actor=r.user, tenant=r.first, user_id=u.pk)


def test_revoked_actor_or_foreign_scope_denied(manager):
    r = manager; u = new_operator(r); payload = edits(r, u)
    with pytest.raises(PermissionDenied): create_operator(actor=r.user, tenant=r.second, username='other', full_name='x', email='', password=PASSWORD)
    r.assignment.role = r.roles['OPERATOR']; r.assignment.save(update_fields=['role'])
    with pytest.raises(PermissionDenied): update_operator(**payload)
    u.refresh_from_db(); assert u.is_active


def test_http_create_list_and_edit_and_csrf_page(manager, client):
    r = manager; client.force_login(r.user)
    assert client.get(reverse('tenants:operator_list')).status_code == 200
    response = client.post(reverse('tenants:operator_create'), {'username': 'http.operator', 'full_name': 'اپراتور وب',
        'password1': PASSWORD, 'password2': PASSWORD, 'tenant': r.second.pk, 'role': r.roles['COMPANY_ADMIN'].pk})
    assert response.status_code == 302
    user = get_user_model().objects.get(username='http.operator')
    assert TenantMembership.objects.get(core_user_id=user.profile.core_user_id).tenant_id == r.first.pk
    assert UserRoleAssignment.objects.get(core_user_id=user.profile.core_user_id).role.role_code == 'OPERATOR'
    page = client.get(reverse('tenants:operator_edit', args=[user.pk]))
    assert page.status_code == 200 and b'csrfmiddlewaretoken' in page.content
    assert b'company_user_list' not in page.content
    assert 'اپراتور وب' in client.get(reverse('tenants:operator_list')).content.decode()


def test_http_operator_cannot_access_admin_endpoints(access_records, client):
    r = access_records; client.force_login(r.user)
    for url in (reverse('tenants:operator_list'), reverse('tenants:operator_create'), reverse('tenants:operator_edit', args=[r.user.pk])):
        assert client.get(url).status_code == 403
        assert client.post(url, {}).status_code in (403, 405)


def test_http_stale_edit_has_conflict_and_no_password_echo(manager, client):
    r = manager; u = new_operator(r); client.force_login(r.user)
    state = operator_edit_state(actor=r.user, tenant=r.first, user_id=u.pk)
    u.username = 'concurrent'; u.save(update_fields=['username'])
    # Inconsistent identity fails closed before form processing, no password shown.
    response = client.post(reverse('tenants:operator_edit', args=[u.pk]), {'username': 'new', 'full_name': 'نام',
        'account_status': 'ACTIVE', 'edit_token': state['token'], 'new_password1': NEW_PASSWORD, 'new_password2': NEW_PASSWORD})
    assert response.status_code == 403 and NEW_PASSWORD not in response.content.decode()


def test_existing_operator_session_rejected_after_password_reset(manager, client):
    r = manager; user = new_operator(r); client.force_login(user)
    assert client.get(reverse('accounts:sessions')).status_code == 200
    update_operator(**edits(r, user, new_password=NEW_PASSWORD))
    response = client.get(reverse('accounts:sessions'))
    assert response.status_code == 302 and '/account/login/' in response.url


def test_existing_operator_session_rejected_after_deactivation(manager, client):
    r = manager; user = new_operator(r); client.force_login(user)
    assert client.get(reverse('accounts:sessions')).status_code == 200
    update_operator(**edits(r, user, account_status='INACTIVE'))
    response = client.get(reverse('accounts:sessions'))
    assert response.status_code == 302 and '/account/login/' in response.url


def test_cannot_modify_another_tenant_using_http(manager, client):
    r = manager; user = new_operator(r)
    core = CoreUser.objects.get(pk=user.profile.core_user_id)
    TenantMembership.objects.create(tenant=r.second, core_user=core, is_active=False)
    client.force_login(r.user)
    assert client.get(reverse('tenants:operator_edit', args=[user.pk])).status_code == 403
    assert 'p03_new' not in client.get(reverse('tenants:operator_list')).content.decode()


def test_superadmin_account_never_a_manager_target(manager):
    r = manager
    with pytest.raises(PermissionDenied):
        operator_edit_state(actor=r.user, tenant=r.first, user_id=r.user.pk)


def test_lock_list_includes_all_privilege_and_identity_tables(monkeypatch):
    from tenants import operator_services as services
    from contextlib import contextmanager
    captured = []
    class Cursor:
        def execute(self, sql): captured.append(sql)
    @contextmanager
    def cursor(): yield Cursor()
    with patch.object(services.connection, 'vendor', 'postgresql'), patch.object(services.connection, 'cursor', side_effect=cursor):
        services._locked_tables()
    assert len(captured) == 1 and 'SHARE ROW EXCLUSIVE MODE' in captured[0]
    models = [get_user_model(), UserProfile, TenantMembership, UserRoleAssignment, CoreUser,
              get_user_model().groups.through, get_user_model().user_permissions.through, Group.permissions.through]
    for model in models:
        assert services.connection.ops.quote_name(model._meta.db_table) in captured[0]
