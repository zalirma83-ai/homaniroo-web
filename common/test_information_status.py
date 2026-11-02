from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from django.template import Context, Template
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from common.information_status import (
    CLEAR_EXPLANATION, LEGACY_REASON, assess_contract, assess_customer,
    attach_customer_information, contract_information_queryset,
    customer_information_queryset, filter_information,
)
from contracts.models import Contract, ContractLine, ContractMonthlyQuantity
from customers.models import CustomerContact, IndustrialUnit, TenantCustomer
from customers.tests import active_customer_tables, prospective_customer_tables
from energy.models import EnergyType, LoadBand


def contract(**kwargs):
    defaults = dict(tenant_id=1, data_quality_status='CLEAN', contract_start_gregorian=date(2026, 3, 21), contract_end_gregorian=date(2027, 3, 20))
    return Contract(**(defaults | kwargs))


def line(**kwargs):
    defaults = dict(tenant_id=1, data_quality_status='CLEAN', contracted_volume=Decimal('10'), volume_unit='MW', volume_unit_status='ASSUMED', base_rate=Decimal('2'), rate_unit='RIAL_KWH', amount_value=Decimal('20'), amount_unit='RIAL')
    return ContractLine(**(defaults | kwargs))


def row(item=None, quantities=()):
    return {'item': item or line(), 'monthly_quantities': list(quantities)}


def customer(**kwargs):
    unit = IndustrialUnit(official_name='هم‌نام', national_id='12', data_quality_status='CLEAN')
    return TenantCustomer(tenant_id=1, tenant_customer_id=1, industrial_unit=unit, data_quality_status='CLEAN', **kwargs)


def contact(**kwargs):
    return CustomerContact(**(dict(tenant_id=1, tenant_customer_id=1, contact_person='نام رابط', mobile_number='0912', data_quality_status='CLEAN') | kwargs))


class InformationStatusTests(SimpleTestCase):
    def test_clean_is_not_approval(self):
        result = assess_contract(contract(), [row()])
        self.assertTrue(result.clear)
        self.assertEqual(result.label, 'بدون هشدار')
        self.assertIn('به معنی تأیید انسانی', result.explanation)

    def test_raw_clean_can_be_incomplete(self):
        result = assess_contract(contract(contract_end_gregorian=None), [row()])
        self.assertTrue(result.incomplete)
        self.assertFalse(result.clear)
        self.assertIn('تاریخ پایان', result.summary)

    def test_dates_are_separate(self):
        result = assess_contract(contract(contract_start_gregorian=None, contract_end_gregorian=None), [row()])
        self.assertEqual({i.code for i in result.issues}, {'start_missing', 'end_missing'})

    def test_no_lines_is_incomplete(self):
        self.assertIn('lines_missing', [i.code for i in assess_contract(contract(), []).issues])

    def test_missing_amount_even_with_rate_and_volume(self):
        result = assess_contract(contract(), [row(line(amount_value=None))])
        self.assertTrue(result.incomplete)
        self.assertFalse(result.review)
        self.assertEqual(result.summary, 'مبلغ کل ثبت نشده')

    def test_both_categories_are_retained(self):
        result = assess_contract(contract(), [row(line(amount_value=None, rate_unit='UNKNOWN'))])
        self.assertTrue(result.incomplete and result.review)
        self.assertEqual(result.label, 'نیازمند تکمیل و نیازمند بررسی')

    def test_zero_is_a_value(self):
        result = assess_contract(contract(), [row(line(contracted_volume=0, amount_value=0, base_rate=0))])
        self.assertTrue(result.clear)

    def test_negative_amount_is_not_reclassified(self):
        self.assertTrue(assess_contract(contract(), [row(line(amount_value=-20))]).clear)

    def test_known_mw_does_not_reopen_assumption(self):
        self.assertTrue(assess_contract(contract(), [row(line(volume_unit_status='ASSUMED'))]).clear)

    def test_unknown_units_only_when_value_exists(self):
        result = assess_contract(contract(), [row(line(base_rate=None, amount_value=None, rate_unit='UNKNOWN', amount_unit='UNKNOWN'))])
        self.assertFalse(result.review)

    def test_every_stored_rate_field_is_checked(self):
        for field in ('base_rate', 'corrected_rate', 'monthly_rate', 'rate_1401', 'adjusted_rate_1401', 'adjusted_rate_1402'):
            with self.subTest(field=field):
                values = {'base_rate': None, 'rate_unit': 'UNKNOWN', field: 2}
                self.assertTrue(assess_contract(contract(), [row(line(**values))]).review)

    def test_missing_volume_specific_reason(self):
        result = assess_contract(contract(), [row(line(contracted_volume=None))])
        self.assertEqual([i.code for i in result.issues], ['volume_missing'])

    def test_unknown_historical_flag_is_not_fabricated(self):
        result = assess_contract(contract(data_quality_status='REVIEW_REQUIRED'), [row()])
        self.assertEqual(result.issues[0].message, LEGACY_REASON)
        self.assertFalse(result.incomplete)

    def test_historical_child_flag_is_visible(self):
        result = assess_contract(contract(), [row(line(data_quality_status='REVIEW_REQUIRED'))])
        self.assertTrue(result.review)
        self.assertEqual(result.issues[0].location, 'ردیف انرژی 1')

    def test_monthly_value_and_period(self):
        q = ContractMonthlyQuantity(quantity_value=None, period_jalali='', quantity_unit='MW', data_quality_status='CLEAN')
        r = row(quantities=[q])
        result = assess_contract(contract(), [r])
        self.assertTrue(result.incomplete)
        self.assertTrue(q.information.incomplete)
        self.assertTrue(r['information'].incomplete)
        self.assertEqual({i.code for i in q.information.issues}, {'monthly_value_missing', 'monthly_period_missing'})

    def test_monthly_unit_and_historical_flag(self):
        q = ContractMonthlyQuantity(quantity_value=0, period_jalali='1405/01', quantity_unit='UNKNOWN', data_quality_status='REVIEW_REQUIRED')
        result = assess_contract(contract(), [row(quantities=[q])])
        self.assertTrue(result.review)
        self.assertFalse(result.incomplete)
        self.assertIn('1405/01', result.issues[0].location)

    def test_missing_contact_approved_rule(self):
        result = assess_customer(customer(), [])
        self.assertTrue(result.incomplete)
        self.assertFalse(result.review)

    def test_contact_person_is_not_a_channel(self):
        self.assertTrue(assess_customer(customer(), [contact(mobile_number=None)]).incomplete)

    def test_any_actual_channel_suffices(self):
        for field in ('mobile_number', 'phone_number', 'email', 'website'):
            values = dict.fromkeys(('mobile_number', 'phone_number', 'email', 'website'))
            values[field] = 'راه تماس'
            with self.subTest(field=field):
                self.assertTrue(assess_customer(customer(), [contact(**values)]).clear)

    def test_whitespace_is_missing(self):
        self.assertTrue(assess_customer(customer(), [contact(mobile_number=' \t\n')]).incomplete)

    def test_foreign_contact_is_ignored(self):
        self.assertTrue(assess_customer(customer(), [contact(tenant_id=2)]).incomplete)
        self.assertTrue(assess_customer(customer(), [contact(tenant_customer_id=2)]).incomplete)

    def test_contact_unexplained_flag_is_not_silently_cleared(self):
        result = assess_customer(customer(), [contact(data_quality_status='REVIEW_REQUIRED')])
        self.assertTrue(result.review)
        self.assertFalse(result.incomplete)
        self.assertEqual(result.issues[0].message, LEGACY_REASON)

    def test_short_national_id_same_name_do_not_reopen_review(self):
        self.assertTrue(assess_customer(customer(), [contact()]).clear)

    def test_customer_unknown_flag_and_missing_contact_both_visible(self):
        c=customer(); c.data_quality_status='REVIEW_REQUIRED'
        result=assess_customer(c, [])
        self.assertTrue(result.incomplete and result.review)
        self.assertEqual(result.issues[-1].message, LEGACY_REASON)

    def test_corrected_known_issue_disappears(self):
        c=contract(); l=line(amount_value=None)
        self.assertTrue(assess_contract(c,[row(l)]).incomplete)
        l.amount_value=20
        self.assertTrue(assess_contract(c,[row(l)]).clear)

    def test_correcting_gap_does_not_clear_unexplained_source_flag(self):
        c=contract(data_quality_status='REVIEW_REQUIRED'); l=line(amount_value=None)
        before=assess_contract(c,[row(l)]); l.amount_value=20
        after=assess_contract(c,[row(l)])
        self.assertTrue(before.incomplete and before.review)
        self.assertFalse(after.incomplete)
        self.assertTrue(after.review)
        self.assertEqual(after.issues[0].message,LEGACY_REASON)

    def test_evaluation_never_updates_fields(self):
        c=contract(data_quality_status='REVIEW_REQUIRED');l=line(amount_value=None)
        a,b=c.__dict__.copy(),l.__dict__.copy()
        assess_contract(c,[row(l)])
        self.assertEqual(a,c.__dict__);self.assertEqual(b,l.__dict__)

    def test_badges_render_both_not_human_approval(self):
        status=assess_contract(contract(),[row(line(amount_value=None,rate_unit='UNKNOWN'))])
        html=Template('{% include "partials/information_badges.html" with information=value %}').render(Context({'value':status}))
        self.assertIn('نیازمند تکمیل',html); self.assertIn('نیازمند بررسی',html)
        self.assertNotIn('تأییدشده',html)

    def test_monthly_location_and_input_are_escaped(self):
        q=ContractMonthlyQuantity(period_jalali='<script>x</script>',quantity_value=None,data_quality_status='CLEAN')
        status=assess_contract(contract(),[row(quantities=[q])])
        html=Template('{% include "partials/information_details.html" with information=value %}').render(Context({'value':status}))
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)

    def test_e04_pages_no_longer_render_quality_partials(self):
        root=Path(__file__).resolve().parent.parent/'templates'
        for rel in ('customers/active_list.html','customers/prospective_list.html','contracts/contract_list.html','customers/active_detail.html','customers/prospective_detail.html','customers/customer_profile.html','contracts/contract_detail.html'):
            with self.subTest(template=rel):
                text=(root/rel).read_text(encoding='utf-8')
                self.assertNotIn('partials/information_badges.html',text)
                self.assertNotIn('partials/information_details.html',text)
                self.assertNotIn("data_quality_status == 'CLEAN'",text)
                self.assertNotIn('تأییدشده',text)

    def test_e04_quality_filters_are_not_offered(self):
        root=Path(__file__).resolve().parent.parent/'templates'
        for rel in ('customers/active_list.html','customers/prospective_list.html','contracts/contract_list.html'):
            with self.subTest(template=rel):
                text=(root/rel).read_text(encoding='utf-8')
                for value in ('CLEAN','INCOMPLETE','REVIEW_REQUIRED'):
                    self.assertNotIn(f'value="{value}"',text)


@pytest.fixture
def information_records(active_customer_tables):
    unit=IndustrialUnit.objects.create(official_name='هم‌نام',normalized_name='same',national_id='12',data_quality_status='CLEAN')
    customer=TenantCustomer.objects.create(tenant_id=1,industrial_unit=unit,customer_status='ACTIVE',data_quality_status='CLEAN')
    energy=EnergyType.objects.create(energy_type_code='INFO_TEST',energy_type_name_fa='برق رک')
    band=LoadBand.objects.create(load_band_code='INFO_TEST',load_band_name_fa='کل بار')
    item=contract(tenant_customer_id=customer.pk,source_fingerprint='info-test',contract_direction='SALE'); item.save()
    item_line=line(contract=item,energy_type=energy,load_band=band);item_line.save()
    return customer,item,item_line


def assert_contract_agreement(item):
    item=contract_information_queryset(Contract.objects.filter(pk=item.pk)).get()
    lines=[row(l,list(ContractMonthlyQuantity.objects.filter(tenant_id=item.tenant_id,contract_line=l))) for l in ContractLine.objects.filter(tenant_id=item.tenant_id,contract=item)]
    status=assess_contract(item,lines)
    assert item.info_incomplete == status.incomplete
    assert item.info_review == status.review
    return item,status


@pytest.mark.django_db
def test_sql_contract_filters_and_live_repair(information_records):
    _,c,l=information_records
    c,status=assert_contract_agreement(c);assert status.clear
    l.amount_value=None;l.rate_unit='UNKNOWN';l.save()
    c,status=assert_contract_agreement(c);assert status.incomplete and status.review
    qs=contract_information_queryset(Contract.objects.filter(tenant_id=1))
    assert list(filter_information(qs,'INCOMPLETE'))==[c]
    assert list(filter_information(qs,'REVIEW_REQUIRED'))==[c]
    assert not filter_information(qs,'CLEAN').exists()
    l.amount_value=20;l.rate_unit='RIAL_KWH';l.save()
    _,status=assert_contract_agreement(c);assert status.clear
    assert filter_information(qs,'CLEAN').exists()


@pytest.mark.django_db
def test_sql_monthly_status_and_tenant_isolation(information_records):
    _,c,l=information_records
    q=ContractMonthlyQuantity.objects.create(tenant_id=2,contract_line=l,period_jalali='',quantity_value=None,data_quality_status='REVIEW_REQUIRED')
    _,status=assert_contract_agreement(c);assert status.clear
    q.tenant_id=1;q.save()
    _,status=assert_contract_agreement(c);assert status.incomplete and status.review
    q.period_jalali='1405/01';q.quantity_value=0;q.quantity_unit='MW';q.data_quality_status='CLEAN';q.save()
    _,status=assert_contract_agreement(c);assert status.clear


@pytest.mark.django_db
def test_sql_foreign_line_never_changes_parent(information_records):
    _,c,l=information_records
    l.tenant_id=2;l.amount_value=None;l.save()
    _,status=assert_contract_agreement(c)
    assert status.incomplete and not status.review
    assert [i.code for i in status.issues]==['lines_missing']


@pytest.mark.django_db
def test_sql_historical_flag_is_not_approval(information_records):
    _,c,l=information_records
    for obj in (c,l):
        obj.data_quality_status='REVIEW_REQUIRED';obj.save()
    _,status=assert_contract_agreement(c)
    assert status.review and not status.incomplete
    assert all(i.message==LEGACY_REASON for i in status.issues)


@pytest.mark.django_db
def test_sql_customer_channel_and_unit_flag(information_records):
    c,_,_=information_records
    def state():
        annotated=customer_information_queryset(TenantCustomer.objects.filter(pk=c.pk)).select_related('industrial_unit').get()
        result=assess_customer(annotated)
        assert annotated.info_incomplete == result.incomplete
        assert annotated.info_review == result.review
        return result
    assert state().incomplete
    p=CustomerContact.objects.create(tenant_id=2,tenant_customer=c,phone_number='021',data_quality_status='CLEAN')
    assert state().incomplete
    p.tenant_id=1;p.phone_number=' \t\n';p.save()
    assert state().incomplete
    p.phone_number='021';p.save()
    assert state().clear
    c.industrial_unit.data_quality_status='REVIEW_REQUIRED';c.industrial_unit.save()
    assert state().review


@pytest.mark.django_db
def test_customer_both_filters_before_pagination(information_records):
    c,_,_=information_records;c.data_quality_status='REVIEW_REQUIRED';c.save()
    qs=customer_information_queryset(TenantCustomer.objects.filter(tenant_id=1))
    assert filter_information(qs,'INCOMPLETE').get()==c
    assert filter_information(qs,'REVIEW_REQUIRED').get()==c
    assert not filter_information(qs,'CLEAN').exists()


@pytest.mark.django_db
def test_customer_assessments_have_no_per_row_queries(information_records):
    c,_,_=information_records
    for _ in range(25):
        TenantCustomer.objects.create(tenant_id=1,industrial_unit=c.industrial_unit,data_quality_status='CLEAN')
    qs=customer_information_queryset(TenantCustomer.objects.filter(tenant_id=1)).select_related('industrial_unit')
    with CaptureQueriesContext(connection) as captured:
        records=attach_customer_information(list(qs))
        assert all(obj.information.incomplete for obj in records)
    assert len(captured)==1


@pytest.mark.django_db
def test_export_uses_derived_status_and_preserves_numbers(information_records):
    from reports.services import export_rows
    c,item,l=information_records
    l.amount_value=None;l.save()
    header,rows=export_rows(N(tenant_id=1),'contracts')
    assert 'وضعیت اطلاعات' in header and 'کیفیت داده' not in header
    assert rows[0][7] == Decimal('10')
    assert rows[0][9] == 'نیازمند تکمیل'
    assert 'مبلغ کل' in rows[0][10]
    header,rows=export_rows(N(tenant_id=1),'customers')
    assert rows[0][3]=='نیازمند تکمیل'
    assert 'راه تماس' in rows[0][-1]


@pytest.mark.django_db
def test_report_counts_one_missing_date_as_incomplete(information_records):
    from reports.services import _contract_metrics
    _,c,_=information_records
    c.contract_end_gregorian=None;c.save()
    assert _contract_metrics(N(tenant_id=1))['missing_dates']==1
    c.contract_end_gregorian=date(2027,3,20);c.save()
    assert _contract_metrics(N(tenant_id=1))['missing_dates']==0
