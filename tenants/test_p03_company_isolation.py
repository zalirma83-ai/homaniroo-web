"""P03-A02: company isolation fails closed; only active superusers are global."""
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from accounts.access import has_tenant_role
from accounts.test_p01_tenant_access import access_records, req
from dashboard.views import system_control
from tenants.middleware import TenantContextMiddleware
from tenants.models import TenantMembership, UserRoleAssignment
from tenants.services import ACTIVE_TENANT_SESSION_KEY, can_access_tenant, get_allowed_tenants, is_global_admin
from tenants.tests import core_tenant_tables
from tenants.views import tenant_list

pytestmark = pytest.mark.django_db


def test_one_company_normal_then_two_fail_closed_even_for_existing_queryset(access_records):
    r = access_records
    assert list(get_allowed_tenants(r.user)) == [r.first]
    assert can_access_tenant(r.user, r.first.pk)
    previously_built = get_allowed_tenants(r.user)
    second = TenantMembership.objects.create(tenant=r.second, core_user=r.core)
    UserRoleAssignment.objects.create(tenant=r.second, core_user=r.core, role=r.roles['OPERATOR'])
    assert list(previously_built) == []
    for tenant in (r.first, r.second):
        assert not can_access_tenant(r.user, tenant.pk)
        assert not has_tenant_role(r.user, tenant, ('OPERATOR',))
    second.is_active = False
    second.save(update_fields=['is_active'])
    assert list(get_allowed_tenants(r.user)) == [r.first]
    assert has_tenant_role(r.user, r.first, ('OPERATOR',))


def test_two_memberships_fail_even_without_second_role_and_company_change_applies_immediately(access_records):
    r = access_records
    TenantMembership.objects.create(tenant=r.second, core_user=r.core)
    assert not get_allowed_tenants(r.user).exists()
    r.second.status = 'INACTIVE'; r.second.save(update_fields=['status'])
    assert list(get_allowed_tenants(r.user)) == [r.first]
    r.second.status = 'ACTIVE'; r.second.save(update_fields=['status'])
    assert not get_allowed_tenants(r.user).exists()


def test_stale_session_is_cleared_for_multi_membership(access_records):
    r = access_records
    TenantMembership.objects.create(tenant=r.second, core_user=r.core)
    request = req(r, method='get')
    request.session[ACTIVE_TENANT_SESSION_KEY] = r.first.pk
    TenantContextMiddleware(lambda request: HttpResponse('ok'))(request)
    assert request.tenant is None
    assert not request.tenant_choices.exists()
    assert ACTIVE_TENANT_SESSION_KEY not in request.session


def test_group_name_is_not_global_access_but_single_company_remains(access_records):
    r = access_records
    r.user.groups.add(Group.objects.get_or_create(name='SUPERADMIN')[0])
    assert not is_global_admin(r.user)
    assert list(get_allowed_tenants(r.user)) == [r.first]
    assert not can_access_tenant(r.user, r.second.pk)
    for view in (tenant_list, system_control):
        with pytest.raises(PermissionDenied):
            view(req(r, method='get'))


def test_active_superuser_can_see_both_inactive_superuser_cannot(access_records):
    r = access_records
    root = get_user_model().objects.create_superuser(username='p03_isolation_root', password='test-only')
    assert is_global_admin(root)
    assert set(get_allowed_tenants(root).values_list('pk', flat=True)) == {r.first.pk, r.second.pk}
    assert all(has_tenant_role(root, tenant, ('OPERATOR',)) for tenant in (r.first, r.second))
    root.is_active = False
    assert not is_global_admin(root)
    assert not get_allowed_tenants(root).exists()
    assert not has_tenant_role(root, r.first, ('OPERATOR',))
    with pytest.raises(PermissionDenied):
        system_control(req(r, user=root, method='get'))


@pytest.mark.parametrize('value', [None, 0, -1, True, False, '1'])
def test_invalid_company_ids_denied(access_records, value):
    assert not can_access_tenant(access_records.user, value)
