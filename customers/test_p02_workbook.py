"""Complete Excel preview and source-display guardrails, isolated test database."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N
import hashlib
import json

import pytest
from django.db import connection,transaction,IntegrityError
from django.core.exceptions import PermissionDenied
from django.template.loader import get_template
from django.template import Context,Template

from customers import p02_workbook as s
from customers.models import CustomerExcelSourceRow
from customers.test_p02_contact_email import records

pytestmark=pytest.mark.django_db(transaction=True)


@pytest.fixture
def excel_data(records):
    table=CustomerExcelSourceRow._meta.db_table
    if connection.vendor=='postgresql':
        with connection.cursor() as c:c.execute('SELECT to_regclass(%s)',[table]);exists=c.fetchone()[0] is not None
    else:exists=table in connection.introspection.table_names()
    if not exists:
        with connection.schema_editor() as editor:editor.create_model(CustomerExcelSourceRow)
    yield records
    CustomerExcelSourceRow.objects.filter(tenant_id__in=[records.tenant.pk,records.other.pk])._raw_delete('default')
    if not exists:
        with connection.schema_editor() as editor:editor.delete_model(CustomerExcelSourceRow)


def row_record(data,**extra):
    values=dict(tenant_id=data.tenant.pk,tenant_customer_id=data.customer.pk,billing_account_id=data.account.pk,
        source_filename='source.xlsx',source_sha256='a'*64,source_sheet='Sheet1',source_row=2,
        raw_cells={'E':9123456789,'H':'0','K':'9-22','L':'متن بدون تاریخ','M':None},
        source_headers={'L':{'type':'date','value':'2026-05-25T00:00:00'}},cell_types={'E':'n','H':'s'},demand_mw=Decimal('0'))
    values.update(extra);return CustomerExcelSourceRow.objects.create(**values)


def test_raw_cells_headers_and_zero_demand_are_preserved(excel_data):
    row=row_record(excel_data);row.refresh_from_db()
    assert row.raw_cells['E']==9123456789 and row.raw_cells['K']=='9-22'
    assert row.raw_cells['M'] is None and row.demand_mw==0
    assert row.source_headers['L']['value']=='2026-05-25T00:00:00'


def test_same_file_row_cannot_be_inserted_twice(excel_data):
    row_record(excel_data)
    with pytest.raises(IntegrityError):
        with transaction.atomic():row_record(excel_data)


def test_source_rows_are_company_and_customer_scoped(excel_data):
    ours=row_record(excel_data)
    row_record(excel_data,tenant_id=excel_data.other.pk,tenant_customer_id=excel_data.other_customer.pk)
    assert [r.pk for r in s.workbook_rows_for_customer(excel_data.tenant,excel_data.customer)]==[ours.pk]
    with pytest.raises(PermissionDenied):s.workbook_rows_for_customer(excel_data.tenant,excel_data.other_customer)


def test_source_cell_labels_do_not_invent_meaning_or_dates(excel_data):
    row_record(excel_data)
    row=s.workbook_rows_for_customer(excel_data.tenant,excel_data.customer)[0]
    labels={c['column']:c['label'] for c in row.display_cells}
    assert set(labels)==set('ABCDEFGHIJKLM')
    assert 'مشخص نیست' in labels['G'] and 'حدس' in labels['K'] and 'تاریخ وقوع' in labels['L']


@pytest.mark.parametrize('value,expected',[(None,None),('',None),('0',Decimal('0')),('5.4',Decimal('5.4')),('1000',Decimal('1000'))])
def test_demand_is_exact_decimal_in_confirmed_mw(value,expected):assert s.demand_value(value)==expected


@pytest.mark.parametrize('value',['NaN','Infinity','-1','5 MW','1.0000001'])
def test_demand_has_no_guessed_conversion(value):
    with pytest.raises(s.ApprovalError):s.demand_value(value)


def test_source_phone_does_not_gain_invented_prefix():
    assert s.source_string(9123456789)=='9123456789'
    assert s.source_string('09123456789')=='09123456789'
    assert s.source_string(10.0)=='10' and s.canonical_text(0)=='0'


def test_multicontact_rows_need_a_unique_match():
    row={'source_cells':{'D':'حميدرضا','E':'0912','F':'031'}}
    one={'contact_id':1,'contact_person':'حمیدرضا','mobile_number':'0912','phone_number':'031'}
    two={'contact_id':2,'contact_person':None,'mobile_number':'0999','phone_number':'032'}
    assert s.choose_contact(row,[one,two])['contact_id']==1
    with pytest.raises(s.ApprovalError):s.choose_contact(row,[one,dict(one,contact_id=3)])


def test_partial_template_compiles_and_escapes_untrusted_text():
    get_template('customers/customer_profile.html');get_template('customers/partials/excel_source_rows.html')
    row=N(source_filename='source.xlsx',source_row=2,billing_account_id=None,source_headers={},demand_mw=Decimal('0'),
          display_cells=[{'column':'G','label':'بدون عنوان','value':'<script>x</script>'}])
    html=get_template('customers/partials/excel_source_rows.html').render({'excel_source_rows':[row]})
    assert '<script>' not in html and '&lt;script&gt;' in html and 'MW' in html


def test_command_is_readonly_by_default_and_planner_never_writes():
    from customers.management.commands.p02_workbook import Command
    parser=Command().create_parser('manage.py','p02_workbook')
    assert parser.get_default('apply') is False
    assert parser.get_default('backup_manifest') is None
    import inspect,ast
    tree=ast.parse(inspect.getsource(s))
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {'save','create','update','delete','bulk_create','get_or_create'} and not (n.func.attr=='update' and isinstance(n.func.value,ast.Name) and n.func.value.id in {'allowed','plan'}) for n in ast.walk(tree))
