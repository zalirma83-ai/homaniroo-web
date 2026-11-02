"""P07: provisional per-company ranking in test database only."""
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory

from contracts.models import Contract
from contracts.tests import contract_management_tables
from customers.models import IndustrialUnit, TenantCustomer
from customers.provisional_rank import attach_provisional_ranks
from customers import views

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def only_disposable_test_database(db):
    name = connection.settings_dict['NAME']
    if connection.vendor == 'postgresql':
        assert name == 'test_homaniroo_dev', 'Only the PostgreSQL test database is allowed.'
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            assert cursor.fetchone()[0] == 'test_homaniroo_dev', 'Unexpected database; stop.'
    else:
        assert connection.vendor == 'sqlite' and (name == ':memory:' or str(name).startswith('file:memorydb_')), 'Only disposable in-memory SQLite is allowed.'

@pytest.fixture
def tenant(): return N(tenant_id=17)

@pytest.fixture
def customer(contract_management_tables, tenant):
    unit=IndustrialUnit.objects.create(official_name='مشتری آزمون',normalized_name='p07')
    return TenantCustomer.objects.create(tenant_id=tenant.tenant_id,industrial_unit=unit,customer_status='ACTIVE')


def add(customer, number, *, tenant_id=None, direction='SALE', status='IMPORTED'):
    return Contract.objects.create(tenant_id=tenant_id if tenant_id is not None else customer.tenant_id,
        tenant_customer_id=customer.pk,contract_direction=direction,contract_number=number,
        contract_status=status,source_fingerprint='p07-test')


@pytest.mark.parametrize('count,expected',[(0,None),(1,1),(2,2),(3,3),(4,4),(5,5),(6,5),(22,5)])
def test_approved_thresholds_all_history(customer,tenant,count,expected):
    for i in range(count):add(customer,'TEST-'+str(i))
    attach_provisional_ranks(tenant,[customer]);rank=customer.provisional_rank
    assert rank['contract_count']==count and rank['value']==expected
    assert not rank['missing_number'] and not rank['repeated_number']


def test_missing_and_duplicate_numbers_flagged_without_merge(customer,tenant):
    for number in (' ABC ', 'abc', None, '', '   ', 'Z'):add(customer,number)
    attach_provisional_ranks(tenant,[customer]);rank=customer.provisional_rank
    assert rank=={'value':5,'contract_count':6,'missing_number':True,'repeated_number':True}


def test_only_same_company_and_sale_count(customer,tenant):
    add(customer,'A');add(customer,'A',tenant_id=18);add(customer,'A',direction='PURCHASE')
    attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank['contract_count']==1
    assert not customer.provisional_rank['repeated_number']


def test_all_recorded_statuses_included_and_dates_not_required(customer,tenant):
    for status in ('DRAFT','ACTIVE','EXPIRED','TERMINATED','IMPORTED'):add(customer,status,status=status)
    attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank['contract_count']==5


def test_non_active_never_ranked(customer,tenant,django_assert_num_queries):
    customer.customer_status='LEAD';customer.save(update_fields=['customer_status']);add(customer,'one')
    with django_assert_num_queries(0):attach_provisional_ranks(tenant,[customer])
    assert customer.provisional_rank is None
    customer.customer_status='DORMANT';attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank is None


@pytest.mark.parametrize('bad',[None,0,-1,True,'17'])
def test_invalid_tenant_rejected(customer,bad):
    with pytest.raises(PermissionDenied):attach_provisional_ranks(bad,[customer])


def test_foreign_customer_rejected(customer):
    with pytest.raises(PermissionDenied):attach_provisional_ranks(N(tenant_id=18),[customer])


def test_one_grouped_query_for_whole_page(customer,tenant,django_assert_num_queries):
    unit=customer.industrial_unit
    others=[TenantCustomer.objects.create(tenant_id=tenant.tenant_id,industrial_unit=unit,customer_status='ACTIVE') for _ in range(24)]
    add(customer,'unique')
    with django_assert_num_queries(1):attach_provisional_ranks(tenant,[customer,*others])
    assert customer.provisional_rank['value']==1 and all(c.provisional_rank['value'] is None for c in others)


def test_partial_reports_basis_and_warnings_without_values(customer,tenant):
    add(customer,' A ');add(customer,'a');add(customer,None);attach_provisional_ranks(tenant,[customer])
    html=render_to_string('customers/partials/provisional_rank.html',{'rank':customer.provisional_rank})
    assert 'رتبه' in html and 'خرید' in html and 'تکراری' in html and 'ثبت نشده' in html
    assert 'موقت' not in html
    assert ' A ' not in html
    html=render_to_string('customers/partials/provisional_rank.html',{'rank':{'value':None,'contract_count':0}})
    assert 'اطلاعات ناکافی' in html


def test_all_three_templates_compile_and_include_correct_status():
    from django.template.loader import get_template
    for template in ('customers/active_list.html','customers/active_detail.html','customers/customer_profile.html'):
        get_template(template)
    from django.conf import settings
    from pathlib import Path
    root=Path(settings.BASE_DIR)/'templates/customers'
    assert 'customers/partials/provisional_rank.html' in (root/'active_list.html').read_text(encoding='utf8')
    assert 'customers/partials/provisional_rank.html' in (root/'active_detail.html').read_text(encoding='utf8')
    profile=(root/'customer_profile.html').read_text(encoding='utf8')
    assert "customer.customer_status == 'ACTIVE'" in profile and 'customers/partials/provisional_rank.html' in profile


def test_active_detail_calculates_rank_and_passes_to_template(customer,tenant):
    add(customer,'unique')
    req=RequestFactory().get('/customers/active/1/');req.tenant=tenant
    req.user=N(is_authenticated=True)
    with patch.object(views,'get_active_customer',return_value=customer), \
         patch.object(views.CustomerContact.objects,'for_tenant') as contacts, \
         patch.object(views,'attach_business_classification'),patch.object(views,'assess_customer'), \
         patch.object(views,'active_contract_rows',return_value={}),patch.object(views,'render',return_value='sentinel') as render:
        contacts.return_value.filter.return_value=[]
        assert views.active_detail(req,customer.pk)=='sentinel'
        assert render.call_args.args[2]['customer'].provisional_rank['value']==1


def test_profile_skips_ranking_for_non_active(customer,tenant):
    customer.customer_status='LEAD';req=RequestFactory().get('/customers/profile/1/');req.tenant=tenant
    req.user=N(is_authenticated=True)
    with patch.object(views,'get_customer_for_profile',return_value=customer), \
         patch.object(views,'attach_business_classification'),patch.object(views,'build_customer_profile',return_value={}), \
         patch.object(views,'source_notes_for_customer',return_value=[]),patch.object(views,'workbook_rows_for_customer',return_value=[]), \
         patch.object(views,'attach_provisional_ranks') as ranks,patch.object(views,'render',return_value='sentinel'):
        assert views.customer_profile(req,customer.pk)=='sentinel';ranks.assert_not_called()


def test_new_contract_recalculates_without_persisting_rank(customer,tenant):
    attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank['value'] is None
    add(customer,'first');attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank['value']==1
    add(customer,'second');attach_provisional_ranks(tenant,[customer]);assert customer.provisional_rank['value']==2
    customer.refresh_from_db();assert customer.customer_status=='ACTIVE'
    assert not any(f.name=='provisional_rank' for f in TenantCustomer._meta.fields)


def test_active_profile_calculates_rank(customer,tenant):
    add(customer,'A');add(customer,'B')
    req=RequestFactory().get('/customers/profile/1/');req.tenant=tenant;req.user=N(is_authenticated=True)
    with patch.object(views,'get_customer_for_profile',return_value=customer), \
         patch.object(views,'attach_business_classification'),patch.object(views,'build_customer_profile',return_value={}), \
         patch.object(views,'source_notes_for_customer',return_value=[]),patch.object(views,'workbook_rows_for_customer',return_value=[]), \
         patch.object(views,'render',return_value='ok') as render:
        assert views.customer_profile(req,customer.pk)=='ok'
        assert render.call_args.args[2]['customer'].provisional_rank['value']==2


def test_active_list_attaches_rank_to_page(customer,tenant):
    from unittest.mock import MagicMock
    add(customer,'A')
    req=RequestFactory().get('/customers/active/');req.tenant=tenant;req.user=N(is_authenticated=True)
    base=MagicMock();base.count.return_value=1;base.values.return_value=[]
    page=N(object_list=[customer])
    with patch.object(views,'active_customers_for_tenant',return_value=base), \
         patch.object(views.Contract.objects,'for_tenant') as contracts, \
         patch.object(views,'filter_information') as fi,patch.object(views,'filter_active_customers',return_value=base), \
         patch.object(views,'classify_customers',return_value=base),patch.object(views,'Paginator') as paginator, \
         patch.object(views,'attach_customer_information',side_effect=lambda x:x),patch.object(views,'render',return_value='ok') as render:
        contracts.return_value.filter.return_value.count.return_value=1
        fi.return_value.count.return_value=0
        paginator.return_value.get_page.return_value=page
        assert views.active_list(req)=='ok'
        assert render.call_args.args[2]['page'].object_list[0].provisional_rank['value']==1
