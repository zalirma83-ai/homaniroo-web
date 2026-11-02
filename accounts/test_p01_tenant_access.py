"""A02/A05: company role isolation, role revocation and direct-route protection."""
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory

from accounts.access import has_tenant_role, superadmin_required
from common.templatetags.jalali import has_tenant_roles
from customers import views as cv, ownership_views as ov
from customers.ownership import can_edit, set_ownership
from customers.partner_notes import can_edit_partner_note
from contracts import views as contracts
from interactions import views as interactions
from energy import views as energy
from reports import views as reports
from tenants.models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from tenants.tests import core_tenant_tables

ROLES = ('COMPANY_ADMIN', 'SENIOR_OPERATOR', 'OPERATOR')
pytestmark = pytest.mark.django_db


@pytest.fixture
def access_records(core_tenant_tables):
    User = get_user_model()
    user = User.objects.create_user(username='p01_operator')
    core = CoreUser.objects.create(full_name='اپراتور آزمون', account_status='ACTIVE')
    user.profile.core_user_id = core.pk
    user.profile.save(update_fields=['core_user_id'])
    first = Tenant.objects.create(tenant_code='P01_A', legal_name='A', display_name='شرکت الف')
    second = Tenant.objects.create(tenant_code='P01_B', legal_name='B', display_name='شرکت ب')
    roles = {code: Role.objects.create(role_code=code, role_name_fa=code) for code in ROLES}
    membership = TenantMembership.objects.create(tenant=first, core_user=core, is_active=True)
    assignment = UserRoleAssignment.objects.create(tenant=first, core_user=core, role=roles['OPERATOR'])
    return N(user=user, core=core, first=first, second=second, roles=roles,
             membership=membership, assignment=assignment)


def req(r, tenant=None, user=None, method='post'):
    request = getattr(RequestFactory(), method)('/customers/prospective/', {})
    request.user = r.user if user is None else user
    request.tenant = r.first if tenant is None else tenant
    request.tenant_choices = [request.tenant] if request.tenant else []
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.parametrize('code', ROLES)
def test_company_roles_work_without_django_global_group(access_records, code):
    r=access_records
    r.assignment.role=r.roles[code];r.assignment.save(update_fields=['role'])
    assert not r.user.groups.exists()
    assert has_tenant_role(r.user,r.first,ROLES)
    assert has_tenant_roles(req(r),','.join(ROLES))
    assert can_edit(r.user,r.first)
    assert can_edit_partner_note(r.user,r.first)


def test_senior_role_in_first_company_does_not_leak_to_second(access_records):
    r=access_records
    r.assignment.role=r.roles['SENIOR_OPERATOR'];r.assignment.save(update_fields=['role'])
    TenantMembership.objects.create(tenant=r.second,core_user=r.core)
    UserRoleAssignment.objects.create(tenant=r.second,core_user=r.core,role=r.roles['OPERATOR'])
    r.user.groups.add(Group.objects.get_or_create(name='SENIOR_OPERATOR')[0])
    # Multiple active company memberships now fail closed in both companies.
    assert not has_tenant_role(r.user,r.first,ROLES[:2])
    assert not has_tenant_role(r.user,r.second,ROLES)
    assert not has_tenant_role(r.user,r.second,ROLES[:2])


@pytest.mark.parametrize('case', ['no_role','global_assignment','other_company_role','inactive_membership',
    'missing_membership','inactive_company','no_profile_link','inactive_user','inactive_superuser','only_global_superadmin'])
def test_incomplete_or_foreign_access_never_grants_permission(access_records,case):
    r=access_records
    r.user.groups.add(Group.objects.get_or_create(name='OPERATOR')[0])
    if case=='no_role': r.assignment.delete()
    elif case=='global_assignment': r.assignment.tenant=None;r.assignment.save(update_fields=['tenant'])
    elif case=='other_company_role': r.assignment.tenant=r.second;r.assignment.save(update_fields=['tenant'])
    elif case=='inactive_membership': r.membership.is_active=False;r.membership.save(update_fields=['is_active'])
    elif case=='missing_membership': r.membership.delete()
    elif case=='inactive_company': Tenant.objects.filter(pk=r.first.pk).update(status='INACTIVE')
    elif case=='no_profile_link': r.user.profile.core_user_id=None;r.user.profile.save(update_fields=['core_user_id'])
    elif case=='inactive_user': r.user.is_active=False
    elif case=='inactive_superuser': r.user.is_superuser=True;r.user.is_active=False
    elif case=='only_global_superadmin':
        r.assignment.delete();r.membership.delete()
        r.user.groups.add(Group.objects.get_or_create(name='SUPERADMIN')[0])
    assert not has_tenant_role(r.user,r.first,ROLES)
    assert not has_tenant_roles(req(r),','.join(ROLES))
    assert not can_edit(r.user,r.first)
    assert not can_edit_partner_note(r.user,r.first)


@pytest.mark.parametrize('tenant',[None,0,-1,True,False,'1',N(tenant_id=None)])
def test_invalid_company_is_not_an_operation_permission(access_records,tenant):
    assert not has_tenant_role(access_records.user,tenant,ROLES)


def test_role_and_membership_revocation_take_effect_on_same_user_object(access_records):
    r=access_records
    assert has_tenant_role(r.user,r.first,ROLES)
    r.assignment.delete()
    assert not has_tenant_role(r.user,r.first,ROLES)
    UserRoleAssignment.objects.create(tenant=r.first,core_user=r.core,role=r.roles['OPERATOR'])
    assert has_tenant_role(r.user,r.first,ROLES)
    r.membership.is_active=False;r.membership.save(update_fields=['is_active'])
    assert not has_tenant_role(r.user,r.first,ROLES)


def test_active_superuser_keeps_both_companies_without_membership(access_records):
    r=access_records
    root=get_user_model().objects.create_superuser(username='p01_root',password='test-only')
    for tenant in (r.first,r.second):
        assert has_tenant_role(root,tenant,ROLES)
        assert has_tenant_role(root,tenant,ROLES[:2])
        assert can_edit_partner_note(root,tenant)


# First callable inside each protected view: proof of permission before business code.
ROUTES = [
    (cv,'prospective_create',(), 'ProspectiveCustomerCreateForm',False),
    (cv,'prospective_edit',(1,), 'get_prospective_customer',False),
    (cv,'contact_add',(1,), 'get_prospective_customer',False),
    (cv,'interaction_add',(1,), 'get_prospective_customer',False),
    (cv,'prospective_convert',(1,), 'get_prospective_customer',True),
    (cv,'customer_mark_dormant',(1,), 'get_status_change_candidate',True),
    (cv,'dormant_restore',(1,), 'get_dormant_customer',True),
    (ov,'ownership_update',(1,), 'accessible_unit',False),
    (contracts,'contract_list',(), 'build_contract_list',False),
    (contracts,'contract_detail',(1,), 'get_contract_detail',False),
    (interactions,'work_queue',(), 'build_work_queue',False),
    (interactions,'interaction_create',(), 'InteractionCreateForm',False),
    (interactions,'follow_up_action',(1,), 'FollowUpActionForm',False),
    (interactions,'follow_up_reschedule',(1,), 'get_open_follow_up',False),
    (interactions,'alert_center',(), 'interactions.alert_services.build_alert_center',False),
    (interactions,'alert_sync',(), 'interactions.alert_services.synchronize_alerts',False),
    (interactions,'alert_action',(1,), 'interactions.forms.AlertStatusForm',False),
    (energy,'trading_dashboard',(), 'build_energy_trading_context',False),
    (reports,'report_center',(), 'DateFilterRangeForm',False),
    (reports,'export_csv',('customers',), 'DateFilterRangeForm',True),
]


@pytest.mark.parametrize('method',['get','post'])
@pytest.mark.parametrize('module,name,args,boundary,senior', ROUTES,ids=[x[1] for x in ROUTES])
def test_global_group_or_other_company_role_cannot_enter_route(access_records,module,name,args,boundary,senior,method):
    r=access_records
    r.user.groups.add(Group.objects.get_or_create(name='COMPANY_ADMIN')[0])
    TenantMembership.objects.create(tenant=r.second,core_user=r.core)
    # OPERATOR role remains in first company only; second company has membership only.
    with patch(boundary if '.' in boundary else module.__name__+'.'+boundary) as service:
        with pytest.raises(PermissionDenied):
            getattr(module,name)(req(r,tenant=r.second,method=method),*args)
        service.assert_not_called()


class ReachedBusinessCode(Exception):
    pass


@pytest.mark.parametrize('kind',['company_role','superuser'])
@pytest.mark.parametrize('module,name,args,boundary,senior', ROUTES,ids=[x[1] for x in ROUTES])
def test_valid_company_role_reaches_business_code(access_records,module,name,args,boundary,senior,kind):
    r=access_records
    if kind=='superuser':
        r.user=get_user_model().objects.create_superuser(username='p01_route_root',password='test-only')
    elif senior:
        r.assignment.role=r.roles['COMPANY_ADMIN'];r.assignment.save(update_fields=['role'])
    with patch(boundary if '.' in boundary else module.__name__+'.'+boundary,side_effect=ReachedBusinessCode) as service:
        with pytest.raises(ReachedBusinessCode):
            getattr(module,name)(req(r),*args)
        service.assert_called_once()


@pytest.mark.parametrize('view',[cv.customer_mark_dormant,cv.dormant_restore])
def test_plain_operator_cannot_change_dormancy_even_with_global_admin_group(access_records,view):
    r=access_records
    r.user.groups.add(Group.objects.get_or_create(name='COMPANY_ADMIN')[0])
    with pytest.raises(PermissionDenied):view(req(r),1)


def test_anonymous_redirect_and_missing_tenant_redirect_are_preserved(access_records):
    r=access_records
    response=cv.prospective_create(req(r,user=AnonymousUser()))
    assert response.status_code==302 and '/account/login/' in response.url
    request=req(r);request.tenant=None
    response=cv.prospective_create(request)
    assert response.status_code==302 and '/companies/select/' in response.url


def test_ownership_service_checks_company_role_before_unit_query(access_records):
    r=access_records
    with patch('customers.ownership.accessible_unit') as lookup:
        with pytest.raises(PermissionDenied):
            set_ownership(tenant=r.second,unit_id=1,value='PUBLIC',user=r.user)
        lookup.assert_not_called()


def test_controls_are_hidden_for_foreign_role_and_visible_for_same_company(access_records):
    r=access_records
    context={'user':r.user,'page':Paginator([],25).get_page(1),'customer':N(tenant_customer_id=11,
             industrial_unit=N(industrial_unit_id=22,official_name='مشتری آزمایشی')),
             'unit':N(industrial_unit_id=22),'ownership':N(ownership_type='UNKNOWN')}
    for tenant,allowed in ((r.first,True),(r.second,False)):
        context['request']=req(r,tenant=tenant,method='get')
        html=render_to_string('customers/prospective_detail.html',context)
        for text in ('ویرایش مشخصات','+ افزودن اطلاعات تماس','+ ثبت تماس یا مذاکره جدید','ذخیره مالکیت'):
            assert (text in html)==allowed
        assert 'پرونده کامل' in html
        html=render_to_string('customers/prospective_list.html',context)
        assert ('+ ثبت مشترک بالقوه' in html)==allowed


def test_credential_management_is_still_superuser_only(access_records):
    r=access_records
    r.assignment.role=r.roles['COMPANY_ADMIN'];r.assignment.save(update_fields=['role'])
    protected=superadmin_required(lambda request:HttpResponse('allowed'))
    with pytest.raises(PermissionDenied):protected(req(r))
