"""P03-A01: only company admin or superuser may change customer status or export."""
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from accounts.test_p01_tenant_access import access_records, req, ReachedBusinessCode
from customers import views as customer_views
from reports import views as report_views
from tenants.models import TenantMembership, UserRoleAssignment
from tenants.tests import core_tenant_tables

pytestmark = pytest.mark.django_db

ROUTES = (
    (customer_views, 'prospective_convert', (1,), 'get_prospective_customer'),
    (customer_views, 'customer_mark_dormant', (1,), 'get_status_change_candidate'),
    (customer_views, 'dormant_restore', (1,), 'get_dormant_customer'),
    (report_views, 'export_csv', ('customers',), 'DateFilterRangeForm'),
)

@pytest.mark.parametrize('method', ['get', 'post'])
@pytest.mark.parametrize('role', ['OPERATOR', 'SENIOR_OPERATOR', 'COMPANY_ADMIN', 'SUPERUSER'])
@pytest.mark.parametrize('module,name,args,boundary', ROUTES, ids=[x[1] for x in ROUTES])
def test_permission_before_business_code(access_records, method, role, module, name, args, boundary):
    r = access_records
    if role == 'SUPERUSER':
        r.user = get_user_model().objects.create_superuser(username='p03_root', password='test-only')
    else:
        r.assignment.role = r.roles[role]
        r.assignment.save(update_fields=['role'])
    expected = ReachedBusinessCode if role in {'COMPANY_ADMIN', 'SUPERUSER'} else PermissionDenied
    with patch(module.__name__ + '.' + boundary, side_effect=ReachedBusinessCode) as called:
        with pytest.raises(expected):
            getattr(module, name)(req(r, method=method), *args)
        if expected is ReachedBusinessCode:
            called.assert_called_once()
        else:
            called.assert_not_called()

@pytest.mark.parametrize('module,name,args,boundary', ROUTES, ids=[x[1] for x in ROUTES])
def test_foreign_role_and_revoked_membership_cannot_enter(access_records, module, name, args, boundary):
    r = access_records
    r.assignment.role = r.roles['COMPANY_ADMIN']; r.assignment.save(update_fields=['role'])
    TenantMembership.objects.create(tenant=r.second, core_user=r.core)
    UserRoleAssignment.objects.create(tenant=r.second, core_user=r.core, role=r.roles['OPERATOR'])
    with patch(module.__name__ + '.' + boundary) as called:
        with pytest.raises(PermissionDenied):
            getattr(module, name)(req(r, tenant=r.second), *args)
        r.membership.is_active = False; r.membership.save(update_fields=['is_active'])
        with pytest.raises(PermissionDenied):
            getattr(module, name)(req(r), *args)
        called.assert_not_called()

@pytest.mark.parametrize('role,allowed', [('OPERATOR', False), ('SENIOR_OPERATOR', False), ('COMPANY_ADMIN', True), ('SUPERUSER', True)])
def test_sensitive_controls_match_server_policy(access_records, role, allowed):
    from types import SimpleNamespace as N
    r = access_records
    if role == 'SUPERUSER':
        r.user = get_user_model().objects.create_superuser(username='p03_ui_root', password='test-only')
    else:
        r.assignment.role = r.roles[role]; r.assignment.save(update_fields=['role'])
    context = {'request': req(r, method='get'), 'user': r.user,
               'customer': N(tenant_customer_id=11, industrial_unit=N(industrial_unit_id=22, official_name='آزمون')),
               'filters': {}, 'energy': {'totals': {}}, 'activity': {}, 'customers': {}, 'contracts': {}}
    lead = render_to_string('customers/prospective_detail.html', context)
    active = render_to_string('customers/active_detail.html', context)
    dormant = render_to_string('customers/dormant_detail.html', context)
    report = render_to_string('reports/report_center.html', context)
    assert ('تبدیل به مشتری فعال' in lead) == allowed
    assert ('انتقال به راکد' in lead) == allowed
    assert ('انتقال به راکد' in active) == allowed
    assert ('ثبت وضعیت جدید' in dormant) == allowed
    assert ('خروجی CSV مشتریان' in report) == allowed
    assert ('CSV تماس‌ها' in report) == allowed
    assert ('خروجی CSV تراز انرژی' in report) == allowed
    assert 'ویرایش مشخصات' in lead


@pytest.mark.parametrize('route', ['audit_center', 'audit_export'])
def test_global_group_name_alone_cannot_grant_audit_access(access_records, route):
    from django.contrib.auth.models import Group
    from auditlog import views as audit_views
    r = access_records
    r.user.groups.add(Group.objects.get_or_create(name='SUPERADMIN')[0])
    boundary = 'build_audit_context' if route == 'audit_center' else 'audit_export_rows'
    with patch('auditlog.views.' + boundary) as service:
        with pytest.raises(PermissionDenied):
            getattr(audit_views, route)(req(r, method='get'))
        service.assert_not_called()
