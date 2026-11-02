"""Test database only; never imports into the live application database."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction, IntegrityError
from django.template import Context, Template

from customers import source_notes as s
from customers.models import CustomerSourceNote
from interactions.models import Interaction
from customers.test_p02_contact_email import records

pytestmark=pytest.mark.django_db(transaction=True)


@pytest.fixture
def data(records,monkeypatch):
    created=[]
    for model in (Interaction,CustomerSourceNote):
        table=model._meta.db_table
        if connection.vendor=='postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT to_regclass(%s)',[table]);exists=cursor.fetchone()[0] is not None
        else:
            exists=table in connection.introspection.table_names()
        if not exists:
            with connection.schema_editor() as editor:editor.create_model(model)
            created.append(model)
    monkeypatch.setattr(s,'TENANT_ID',records.tenant.pk)
    p={'source_row':200,'body':'متن اصلی\nبدون تاریخ','body_sha256':hashlib.sha256('متن اصلی\nبدون تاریخ'.encode()).hexdigest(),
       'customer_id':records.customer.pk,'billing_account_id':records.account.pk,'bill_id':records.account.bill_id,
       'industrial_unit_id':records.unit.pk,'official_name':records.unit.official_name,'customer_status':'LEAD'}
    records.plan=[p]
    yield records
    CustomerSourceNote.objects.filter(tenant_id__in=[records.tenant.pk,records.other.pk])._raw_delete('default')
    Interaction.objects.filter(tenant_id__in=[records.tenant.pk,records.other.pk])._raw_delete('default')
    for model in reversed(created):
        with connection.schema_editor() as editor:editor.delete_model(model)


def make_note(data,**overrides):
    p=data.plan[0]
    values=dict(tenant_id=data.tenant.pk,tenant_customer_id=data.customer.pk,billing_account_id=data.account.pk,
        body=p['body'],body_sha256=p['body_sha256'],source_filename='آذربایجان (1).xlsx',source_sha256=s.SOURCE_SHA256,
        source_sheet='Sheet1',source_row=p['source_row'],source_column='L')
    values.update(overrides)
    return CustomerSourceNote.objects.create(**values)


def test_preview_does_not_write_or_change_customer_status(data):
    before=list(Interaction.objects.values());result=s.inspect_targets(data.plan)
    assert result['notes_to_insert']==1 and result['new_interactions']==0 and result['new_customers']==0
    assert result['status_changes']==0 and not CustomerSourceNote.objects.exists()
    assert list(Interaction.objects.values())==before
    data.customer.refresh_from_db();assert data.customer.customer_status=='LEAD'


def test_status_change_requires_re_review_not_overwrite(data):
    data.customer.customer_status='ACTIVE';data.customer.save(update_fields=['customer_status'])
    with pytest.raises(s.ApprovalError,match='identity/status changed'):s.inspect_targets(data.plan)
    data.customer.refresh_from_db();assert data.customer.customer_status=='ACTIVE'


def test_other_company_cannot_supply_target_or_bill(data):
    plan=[dict(data.plan[0],customer_id=data.other_customer.pk)]
    with pytest.raises(Exception):s.inspect_targets(plan)
    assert not CustomerSourceNote.objects.exists()


def test_existing_interaction_text_is_not_imported_again(data):
    Interaction.objects.create(tenant_id=data.tenant.pk,tenant_customer_id=data.customer.pk,interaction_type='CALL',
        interaction_status='متن اصلی بدون تاریخ',notes=None,source_system='TEST')
    with pytest.raises(s.ApprovalError,match='now exists'):s.inspect_targets(data.plan)


def test_other_company_interaction_does_not_block_target(data):
    Interaction.objects.create(tenant_id=data.other.pk,tenant_customer_id=data.other_customer.pk,interaction_type='CALL',
        interaction_status=data.plan[0]['body'],source_system='TEST')
    assert s.inspect_targets(data.plan)['notes_to_insert']==1


def test_origin_uniqueness_and_explicit_duplicate_stop(data):
    make_note(data)
    with pytest.raises(IntegrityError):
        with transaction.atomic():make_note(data)
    with pytest.raises(s.ApprovalError,match='already exists'):s.inspect_targets(data.plan)


def test_source_note_view_is_company_and_customer_scoped(data):
    note=make_note(data)
    make_note(data,tenant_id=data.other.pk,tenant_customer_id=data.other_customer.pk)
    assert list(s.source_notes_for_customer(data.tenant,data.customer))==[note]
    with pytest.raises(PermissionDenied):s.source_notes_for_customer(data.tenant,data.other_customer)


def test_source_note_preserves_raw_text_without_occurrence_date(data):
    note=make_note(data);note.refresh_from_db()
    assert note.body==data.plan[0]['body']
    assert 'interaction_at' not in {f.name for f in CustomerSourceNote._meta.fields}
    assert 'imported_at' in {f.name for f in CustomerSourceNote._meta.fields}
    assert not Interaction.objects.exists()


def test_preview_fingerprint_changes_when_target_changes(data):
    first=s.inspect_targets(data.plan)['snapshot_sha256']
    data.account.consumer_code='new';data.account.save(update_fields=['consumer_code'])
    assert first!=s.inspect_targets(data.plan)['snapshot_sha256']


def test_profile_template_compiles_and_note_is_html_escaped():
    from django.template.loader import get_template
    get_template('customers/customer_profile.html')
    html=Template('{{ note.body|linebreaksbr }}').render(Context({'note':SimpleNamespace(body='<script>x</script>\nمتن')}))
    assert '<script>' not in html and '&lt;script&gt;' in html and '<br>' in html


def test_command_defaults_to_readonly_and_apply_requires_backup():
    from customers.management.commands.p02_source_notes import Command
    parser=Command().create_parser('manage.py','p02_source_notes')
    options=parser.parse_args(['--report','r','--input','x'])
    assert options.apply is False and options.backup_manifest is None
    assert '--apply' in parser._option_string_actions
