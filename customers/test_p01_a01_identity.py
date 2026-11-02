"""A01: name collisions must never silently attach an existing identity."""
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from .models import CustomerContact, IndustrialUnit, TenantCustomer
from .services import create_prospective_customer, normalize_company_name
from .tests import customer_payload, prospective_customer_tables
from .views import prospective_create

pytestmark = pytest.mark.django_db


def snapshot():
    # Test database only. Compare content, not just counts or sequence values.
    return tuple(list(model.objects.order_by(model._meta.pk.name).values())
                 for model in (IndustrialUnit, TenantCustomer, CustomerContact))


def existing_unit(name='شرکت یکسان', national_id='00112233445'):
    return IndustrialUnit.objects.create(official_name=name,
        normalized_name=normalize_company_name(name), national_id=national_id,
        economic_code='ORIGINAL_CODE', address='ORIGINAL_ADDRESS',
        ownership_type='PRIVATE', data_quality_status='CLEAN')


@pytest.mark.parametrize('existing_tenant', [None, 1, 2])
@pytest.mark.parametrize('input_id', ['', '00112233445', '99887766554'])
@pytest.mark.parametrize('matches', [1, 2])
def test_any_name_collision_stops_without_writing_or_reusing_identity(
        prospective_customer_tables, existing_tenant, input_id, matches):
    for index in range(matches):
        unit=existing_unit(national_id='00112233445' if index==0 else 'OTHER_ID')
        if existing_tenant is not None:
            TenantCustomer.objects.create(tenant_id=existing_tenant,
                industrial_unit=unit, customer_status='ACTIVE')
    before=snapshot()
    payload=customer_payload('شرکت یکسان')
    payload.update(national_id=input_id, address='NEW_ADDRESS', economic_code='NEW_CODE')
    with pytest.raises(ValidationError) as error:
        create_prospective_customer(tenant=N(tenant_id=1), cleaned_data=payload)
    assert error.value.code=='identity_review_required'
    assert snapshot()==before
    # No IDs, other company's name or old identifying data in the message.
    assert 'ORIGINAL_ADDRESS' not in error.value.message
    assert '00112233445' not in error.value.message
    assert 'NEW_CODE' not in error.value.message


@pytest.mark.parametrize('name', ['شرکت یکسان', '  شرکت   یکسان  ', 'شركت يكسان', 'شرکت\u200cیکسان'])
def test_normalized_name_variants_do_not_bypass_guard(prospective_customer_tables, name):
    existing_unit()
    before=snapshot()
    with pytest.raises(ValidationError) as error:
        create_prospective_customer(tenant=N(tenant_id=2), cleaned_data=customer_payload(name))
    assert error.value.code=='identity_review_required'
    assert snapshot()==before


def test_unmatched_name_creates_new_identity_and_contact_without_altering_existing(
        prospective_customer_tables):
    old=existing_unit();before=snapshot()
    payload=customer_payload('شرکت کاملاً جدید')
    payload.update(national_id='00012345678')
    customer=create_prospective_customer(tenant=N(tenant_id=2),cleaned_data=payload)
    assert customer.pk is not None
    assert customer.industrial_unit_id!=old.pk
    assert customer.tenant_id==2 and customer.customer_status=='LEAD'
    assert customer.industrial_unit.national_id=='00012345678'
    assert customer.industrial_unit.normalized_name==normalize_company_name(payload['official_name'])
    assert list(IndustrialUnit.objects.filter(pk=old.pk).values())==before[0]
    contact=CustomerContact.objects.get(tenant_customer=customer)
    assert contact.tenant_id==2 and contact.is_primary
    assert contact.mobile_number==payload['mobile_number']


def test_contact_failure_rolls_back_new_identity_and_membership(prospective_customer_tables):
    before=snapshot()
    with patch('customers.services.CustomerContact.objects.create', side_effect=RuntimeError('test failure')):
        with pytest.raises(RuntimeError,match='test failure'):
            create_prospective_customer(tenant=N(tenant_id=1),
                cleaned_data=customer_payload('شرکت جدید برای آزمون تراکنش'))
    assert snapshot()==before


def test_create_page_shows_review_error_and_preserves_input_without_new_membership(
        prospective_customer_tables):
    unit=existing_unit()
    TenantCustomer.objects.create(tenant_id=2,industrial_unit=unit,customer_status='ACTIVE')
    before=snapshot()
    req=RequestFactory().post('/customers/prospective/new/',customer_payload('شرکت یکسان'))
    req.user=N(is_authenticated=True,is_active=True,is_superuser=True,is_staff=True,
               username='qa',get_full_name=lambda:'اپراتور آزمایشی')
    req.tenant=N(tenant_id=1,display_name='شرکت آزمایشی')
    req.tenant_choices=[req.tenant];req.session={};req._messages=FallbackStorage(req)
    response=prospective_create(req)
    html=response.content.decode()
    assert response.status_code==200
    assert 'تطبیق هویت و تأیید مسئول سامانه لازم است' in html
    assert 'شرکت یکسان' in html and '09120000000' in html
    assert 'مشترک بالقوه با موفقیت ثبت شد.' not in html
    assert 'ORIGINAL_ADDRESS' not in html and '00112233445' not in html
    assert snapshot()==before
