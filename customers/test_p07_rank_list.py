"""P07 ranking page, filters, security and tenant boundaries."""
from types import SimpleNamespace as N
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse

from tenants.models import CoreUser,Role,Tenant,TenantMembership,UserRoleAssignment
from tenants.tests import core_tenant_tables
from customers.models import IndustrialUnit,TenantCustomer
from customers.tests import prospective_customer_tables
from contracts.models import Contract
from contracts.tests import contract_management_tables

pytestmark=pytest.mark.django_db

@pytest.fixture(autouse=True)
def only_disposable_test_database(db):
    name=connection.settings_dict['NAME']
    if connection.vendor=='postgresql':
        assert name=='test_homaniroo_dev', 'Only the PostgreSQL test database is allowed.'
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            assert cursor.fetchone()[0]=='test_homaniroo_dev', 'Unexpected database; stop.'
    else:
        assert connection.vendor=='sqlite' and (name==':memory:' or str(name).startswith('file:memorydb_'))

@pytest.fixture
def records(core_tenant_tables,prospective_customer_tables,contract_management_tables):
    first=Tenant.objects.create(tenant_code='P07_A',legal_name='A',display_name='شرکت الف')
    second=Tenant.objects.create(tenant_code='P07_B',legal_name='B',display_name='شرکت ب')
    role=Role.objects.create(role_code='OPERATOR',role_name_fa='اپراتور')
    def make_user(name,company):
        u=get_user_model().objects.create_user(username=name,password='Test-Only-P07!2026')
        core=CoreUser.objects.create(username=name,full_name=name,account_status='ACTIVE')
        u.profile.core_user_id=core.pk;u.profile.save(update_fields=['core_user_id'])
        TenantMembership.objects.create(tenant=company,core_user=core,is_active=True)
        UserRoleAssignment.objects.create(tenant=company,core_user=core,role=role)
        return u
    ua=make_user('p07_operator_a',first);ub=make_user('p07_operator_b',second)
    def add_customer(company,name,count,status='ACTIVE'):
        unit=IndustrialUnit.objects.create(official_name=name,normalized_name=name,data_quality_status='CLEAN')
        cust=TenantCustomer.objects.create(tenant_id=company.pk,industrial_unit=unit,customer_status=status,data_quality_status='CLEAN')
        for i in range(count):
            Contract.objects.create(tenant_id=company.pk,tenant_customer_id=cust.pk,contract_direction='SALE',
                contract_number=f'P07-{name}-{i}',contract_status='IMPORTED',source_fingerprint='p07-test')
        return cust
    for i in range(6):add_customer(first,f'شرکت الف رتبه {i}',i)
    add_customer(first,'شرکت الف رتبه ۶',6)
    add_customer(first,'مشتری بالقوه الف',8,status='LEAD')
    add_customer(second,'شرکت ب محرمانه',3)
    return N(first=first,second=second,ua=ua,ub=ub,add=add_customer)


def request_as(user,params=None):
    client=Client();client.force_login(user)
    return client.get(reverse('customers:provisional_rank_list'),params or {})

@pytest.mark.parametrize('rank,expected,excluded',[
 ('1','شرکت الف رتبه 1','شرکت الف رتبه 2'),('2','شرکت الف رتبه 2','شرکت الف رتبه 1'),
 ('3','شرکت الف رتبه 3','شرکت الف رتبه 2'),('4','شرکت الف رتبه 4','شرکت الف رتبه 3'),
 ('5','شرکت الف رتبه 5','شرکت الف رتبه 4'),('unknown','شرکت الف رتبه 0','شرکت الف رتبه 1')])
def test_rank_filter_matches_display_and_tenant(records,rank,expected,excluded):
    response=request_as(records.ua,{'rank':rank});html=response.content.decode()
    assert response.status_code==200 and expected in html and excluded not in html
    assert 'شرکت ب محرمانه' not in html and 'مشتری بالقوه الف' not in html
    if rank=='5':assert 'شرکت الف رتبه ۶' in html
    if rank=='unknown':assert 'اطلاعات ناکافی' in html


def test_second_company_scope(records):
    response=request_as(records.ub);html=response.content.decode()
    assert response.status_code==200 and 'شرکت ب محرمانه' in html and 'شرکت الف رتبه 1' not in html


def test_search_and_escaping(records):
    response=request_as(records.ua,{'q':'رتبه 2'});html=response.content.decode()
    assert response.status_code==200 and 'شرکت الف رتبه 2' in html and 'شرکت الف رتبه 3' not in html
    bad=request_as(records.ua,{'q':'<script>bad</script>'}).content.decode()
    assert '<script>bad</script>' not in bad


def test_invalid_rank_and_mutating_method_rejected(records):
    c=Client();c.force_login(records.ua)
    assert c.get(reverse('customers:provisional_rank_list'),{'rank':'99'}).status_code==400
    assert c.post(reverse('customers:provisional_rank_list'),{}).status_code==405


def test_anonymous_redirects_to_login():
    response=Client().get(reverse('customers:provisional_rank_list'))
    assert response.status_code==302 and '/account/login/' in response.url


def test_pagination_keeps_rank_filter(records):
    for i in range(26):records.add(records.first,f'صفحه‌بندی {i:02}',1)
    response=request_as(records.ua,{'rank':'1','page':'2'});html=response.content.decode()
    assert response.status_code==200 and 'rank=1' in html
    assert 'شرکت ب محرمانه' not in html and 'رتبه' in html
    assert 'موقت' not in html


def test_duplicate_warning_is_scoped_and_not_a_merge(records):
    customer=records.add(records.first,'شماره تکراری آزمون',2)
    Contract.objects.filter(tenant_id=records.first.pk,tenant_customer_id=customer.pk).update(contract_number='repeated')
    response=request_as(records.ua,{'q':'شماره تکراری آزمون'});html=response.content.decode()
    assert response.status_code==200 and 'شماره تکراری قرارداد' in html
    assert Contract.objects.filter(tenant_id=records.first.pk,tenant_customer_id=customer.pk).count()==2
