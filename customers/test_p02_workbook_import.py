"""Synthetic isolated database only; no workbook contact data or production DB access."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch
import hashlib,json
import pytest
from django.db import connection,transaction
from django.core.management import call_command
from django.core.management.base import CommandError
from customers import p02_workbook_import as s
from customers import p02_workbook as planner
from customers.models import CustomerExcelSourceRow,CustomerSourceNote,CustomerContact,IndustrialUnit,BillingAccount
from customers.test_p02_contact_email import records
from auditlog.models import AuditLog

pytestmark=pytest.mark.django_db(transaction=True)


@pytest.fixture
def batch(records,monkeypatch):
    created=[]
    for model in (CustomerSourceNote,CustomerExcelSourceRow):
        if connection.vendor=='postgresql':
            with connection.cursor() as c:c.execute('SELECT to_regclass(%s)',[model._meta.db_table]);exists=c.fetchone()[0] is not None
        else:exists=model._meta.db_table in connection.introspection.table_names()
        if not exists:
            with connection.schema_editor() as e:e.create_model(model)
            created.append(model)
    monkeypatch.setattr(s,'TENANT_ID',records.tenant.pk)
    def state(source):
        return {'tenant':planner._image(records.tenant),'customers':[],
            'units':list(IndustrialUnit.objects.filter(pk=records.unit.pk).values()),'other_memberships':[],
            'accounts':list(BillingAccount.objects.filter(pk=records.account.pk).values()),
            'contacts':list(CustomerContact.objects.filter(pk=records.contact.pk).values()),
            'notes':list(CustomerSourceNote.objects.filter(tenant_id=records.tenant.pk).order_by('source_row').values()),'interactions':[]}
    monkeypatch.setattr(planner,'read_state',state)
    before=state(None);monkeypatch.setattr(s,'APPROVED_SNAPSHOT',planner._digest(before))
    plan={'operation':s.OPERATION,'source_rows':[
        {'tenant_customer_id':records.customer.pk,'billing_account_id':records.account.pk,'source_row':2,
         'raw_cells':{'E':9123456789,'G':'بدون عنوان','H':0,'K':'9-22','L':'متن منبع','M':None},
         'source_headers':{'L':{'type':'date','value':'2026-05-25'}},'cell_types':{'E':'n'},'demand_mw':Decimal('0')},
        {'tenant_customer_id':records.customer.pk,'billing_account_id':None,'source_row':3,
         'raw_cells':{'B':None,'H':None},'source_headers':{},'cell_types':{},'demand_mw':None}],
        'updates':[{'model':'CustomerContact','pk':records.contact.pk,'before':{'email':None},'after':{'email':'test@example.com'}}],
        'new_notes':[{'tenant_customer_id':records.customer.pk,'billing_account_id':records.account.pk,'source_row':2,'body':'متن منبع'}],
        'summary':{'test_only':True}}
    yield N(records=records,plan=plan,before=before,source={})
    for model in (CustomerExcelSourceRow,CustomerSourceNote):model.objects.filter(tenant_id=records.tenant.pk)._raw_delete('default')
    for model in reversed(created):
        with connection.schema_editor() as e:e.delete_model(model)


def snapshot(b):
    return {'state':planner.read_state(b.source),'rows':list(s.scope_rows().values()),'audits':list(s.operation_events().values())}


def run(b):
    with transaction.atomic():return s._write_batch(b.source,b.plan,b.before)


def test_atomic_batch_preserves_raw_zero_missing_and_company(batch):
    other=planner._image(batch.records.other_contact)
    result=run(batch)
    assert result['state']=='APPLIED' and result['audit_created']==5
    assert s.verify_completed(batch.source,batch.plan)
    rows=list(s.scope_rows().order_by('source_row'))
    assert rows[0].raw_cells['E']==9123456789 and rows[0].raw_cells['K']=='9-22'
    assert rows[0].demand_mw==0 and rows[1].demand_mw is None and rows[1].billing_account_id is None
    batch.records.other_contact.refresh_from_db();assert planner._image(batch.records.other_contact)==other
    assert all(e.auth_user_id is None and e.username=='' for e in s.operation_events())
    assert 'متن منبع' not in json.dumps([e.metadata for e in s.operation_events()],ensure_ascii=False)


def test_verified_retry_makes_no_write(batch):
    run(batch);before=snapshot(batch)
    assert s.check_ready(batch.source,batch.plan)[1]=='ALREADY_APPLIED'
    assert snapshot(batch)==before


@pytest.mark.parametrize('fail_at',[1,2,3,4,5])
def test_audit_failure_at_each_stage_rolls_back_all(batch,fail_at):
    before=snapshot(batch);real=s.record_audit_event;count=[0]
    def fail(**kwargs):
        count[0]+=1
        return None if count[0]==fail_at else real(**kwargs)
    with patch.object(s,'record_audit_event',side_effect=fail),pytest.raises(s.ApprovalError):run(batch)
    assert snapshot(batch)==before


def test_row_creation_failure_rolls_back_core_notes_and_audit(batch):
    before=snapshot(batch);real=CustomerExcelSourceRow.objects.create;count=[0]
    def fail(**kwargs):
        count[0]+=1
        if count[0]==2:raise RuntimeError('injected row failure')
        return real(**kwargs)
    with patch.object(CustomerExcelSourceRow.objects,'create',side_effect=fail),pytest.raises(RuntimeError):run(batch)
    assert snapshot(batch)==before


def test_partial_prior_import_stops_without_retry(batch):
    CustomerExcelSourceRow.objects.create(**s.row_values(batch.plan['source_rows'][0]));before=snapshot(batch)
    with pytest.raises(s.ApprovalError,match='Partial'):s.check_ready(batch.source,batch.plan)
    assert snapshot(batch)==before


@pytest.mark.parametrize('kind',['row','note','audit','core'])
def test_changed_post_import_data_is_not_overwritten(batch,kind):
    run(batch)
    if kind=='row':s.scope_rows().filter(source_row=2).update(raw_cells={'E':'changed'})
    elif kind=='note':CustomerSourceNote.objects.filter(tenant_id=s.TENANT_ID).update(body='changed')
    elif kind=='audit':s.operation_events().filter(metadata__kind='ROW').first().delete()
    else:CustomerContact.objects.filter(pk=batch.records.contact.pk).update(email='changed@example.com')
    before=snapshot(batch)
    with pytest.raises(s.ApprovalError):s.check_ready(batch.source,batch.plan)
    assert snapshot(batch)==before


def test_unapproved_status_field_rolls_back(batch):
    batch.plan['updates'][0]['after']['customer_status']='ACTIVE';before=snapshot(batch)
    with pytest.raises(s.ApprovalError):run(batch)
    assert snapshot(batch)==before


def test_different_before_image_blocks_write(batch):
    CustomerContact.objects.filter(pk=batch.records.contact.pk).update(email='new@example.com');before=snapshot(batch)
    with pytest.raises(s.ApprovalError):s.check_ready(batch.source,batch.plan)
    assert snapshot(batch)==before


def test_other_company_target_cannot_be_written(batch):
    batch.plan['updates'][0]['pk']=batch.records.other_contact.pk;before=snapshot(batch)
    with pytest.raises(s.ApprovalError):run(batch)
    assert snapshot(batch)==before


def test_post_write_unexpected_change_rolls_back(batch):
    before=snapshot(batch);real=planner.read_state;calls=[0]
    def drift(source):
        state=real(source);state['interactions']=[{'unexpected':True}];return state
    with patch.object(planner,'read_state',side_effect=drift),pytest.raises(s.ApprovalError):run(batch)
    assert snapshot(batch)==before


def test_wrong_approval_file_never_authorizes(tmp_path):
    p=tmp_path/'preview.json';p.write_text('{}')
    with pytest.raises(s.ApprovalError):s.load_approval(p)


def receipt_fixture(tmp_path,before,**extra):
    dump=tmp_path/'backup.dump';dump.write_bytes(b'PGDMP synthetic test archive')
    r={'status':'VERIFIED','operation':s.OPERATION,'source_head':s.HEAD,'source_sha256':s.SOURCE_SHA256,
       'database':'homaniroo_dev','endpoint_fingerprint':planner.ENDPOINT,'schema_fingerprint':before['schema_fingerprint'],
       'approved_preview_sha256':s.PREVIEW_SHA,'plan_sha256':before['plan_sha256'],
       'before_snapshot_sha256':s.APPROVED_SNAPSHOT,'after_snapshot_sha256':s.APPROVED_SNAPSHOT,
       'archive_list_verified':True,'migration_executed':False,'restore_executed':False,'workbook_imported':False,
       'database_changed_by_helper':False,'source_changed':False,'schema_identity_constraints_verified':True,
       'created_at_utc':datetime.now(timezone.utc).isoformat(),'backup_path':str(dump),'size_bytes':dump.stat().st_size,
       'sha256':planner.sha_file(dump),'archive_list_sha256':hashlib.sha256(b'1; mock list').hexdigest(),**extra}
    p=tmp_path/'backup_manifest.json';p.write_text(json.dumps(r));return p


@pytest.mark.parametrize('field,value',[('operation','OLD_SCHEMA_BACKUP'),('schema_fingerprint','wrong'),('source_head','wrong'),('before_snapshot_sha256','wrong'),('archive_list_verified',False),('workbook_imported',True)])
def test_wrong_backup_receipt_stops_before_write(tmp_path,field,value):
    before={'schema_fingerprint':'schema','plan_sha256':'plan'};p=receipt_fixture(tmp_path,before,**{field:value})
    with pytest.raises(s.ApprovalError):s.validate_backup(p,tmp_path/'project',before)


def test_stale_backup_rejected(tmp_path):
    before={'schema_fingerprint':'schema','plan_sha256':'plan'}
    p=receipt_fixture(tmp_path,before,created_at_utc=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat())
    with pytest.raises(s.ApprovalError,match='15 minutes'):s.validate_backup(p,tmp_path/'project',before)


def test_valid_backup_and_list_rechecked_without_restore(tmp_path):
    before={'schema_fingerprint':'schema','plan_sha256':'plan'};p=receipt_fixture(tmp_path,before)
    real=Path.is_file
    def exists(path):return True if str(path).endswith('pg_restore.exe') else real(path)
    with patch.object(Path,'is_file',exists),patch.object(s.subprocess,'run',return_value=N(returncode=0,stdout=b'1; mock list')) as call:
        s.validate_backup(p,tmp_path/'project',before)
        assert call.call_args.args[0][1]=='--list'


def test_archive_checksum_corruption_rejected(tmp_path):
    before={'schema_fingerprint':'schema','plan_sha256':'plan'};p=receipt_fixture(tmp_path,before)
    dump=tmp_path/'backup.dump';data=dump.read_bytes();dump.write_bytes(data[:-1]+b'X')
    with pytest.raises(s.ApprovalError,match='checksum'):s.validate_backup(p,tmp_path/'project',before)


def test_public_apply_requires_backup_before_any_write(batch,monkeypatch):
    monkeypatch.setattr(planner,'verify_runtime_source',lambda:Path('/tmp/project'))
    monkeypatch.setattr(s,'inspect_import',lambda *args:{'state':'READY'})
    with patch.object(s,'_write_batch') as write,pytest.raises(s.ApprovalError,match='backup-manifest'):
        s.apply_import(batch.source,batch.plan,None)
    write.assert_not_called()


def test_default_command_never_applies(batch,monkeypatch,tmp_path):
    from customers.management.commands.p02_workbook import Command
    command=Command();options=vars(command.create_parser('manage.py','p02_workbook').parse_args([]))
    assert options['apply'] is False and options['backup_manifest'] is None
    monkeypatch.setattr(planner,'verify_runtime_source',lambda:None)
    monkeypatch.setattr(planner,'load_source',lambda *a:batch.source)
    monkeypatch.setattr(s,'load_approval',lambda *a:batch.plan)
    monkeypatch.setattr(s,'inspect_import',lambda *a:{'state':'READY'})
    with patch('pathlib.Path.home',return_value=tmp_path),patch.object(s,'apply_import') as apply:
        command.handle(**options)
    apply.assert_not_called()


@pytest.mark.parametrize('fault',[None,'identity','unique','trigger','core_schema','pending','nullable','fk','precision'])
def test_postgresql_schema_catalog_guards(monkeypatch,fault):
    from contextlib import contextmanager
    fake=N(vendor='postgresql',data_types={'BigAutoField':'bigint','AutoField':'integer','BigIntegerField':'bigint',
        'PositiveBigIntegerField':'bigint','IntegerField':'integer','PositiveIntegerField':'integer',
        'CharField':'varchar(%(max_length)s)','TextField':'text','DateTimeField':'timestamp with time zone',
        'DateField':'date','BooleanField':'boolean','JSONField':'jsonb','DecimalField':'numeric(%(max_digits)s, %(decimal_places)s)'},
        ops=N(quote_name=lambda s:s),display_name='Mock PostgreSQL')
    monkeypatch.setattr(s,'connection',fake)
    class Executor:
        def __init__(self,c):self.loader=N(check_consistent_history=lambda c:None,graph=N(leaf_nodes=lambda:[]))
        def migration_plan(self,n):return [N()] if fault=='pending' else []
    monkeypatch.setattr(s,'MigrationExecutor',Executor)
    def query(sql,params=None):
        if 'current_database()' in sql:return [{'db':'homaniroo_dev','version':'170006'}]
        if 'FROM unnest' in sql or 'unnest(%s' in sql:
            return [{'convalidated':False}] if fault=='fk' else []
        table=params[-1] if 'FROM pg_attribute a LEFT' in sql else params[0]
        core=next((e for e in s.EXPECTED_CORE_SCHEMA if e['table']==table),None)
        if 'FROM pg_attribute a LEFT' in sql:
            return [] if fault=='core_schema' else core['columns']
        if 'FROM pg_constraint' in sql and core:return core['constraints']
        if 'FROM pg_trigger' in sql:return [{'tgname':'custom'}] if fault=='trigger' else []
        model=next(m for m in (CustomerSourceNote,CustomerExcelSourceRow,s.Interaction) if m._meta.db_table==table)
        if 'FROM pg_attribute' in sql:
            rows=[{'attname':f.column,'type':f.db_type(fake).replace('varchar(','character varying(').replace(', ', ','),
                'attnotnull':not f.null,'attidentity':'d' if f.primary_key else ''} for f in model._meta.local_fields]
            if fault=='identity':rows[0]['attidentity']=''
            if fault=='nullable':rows[0]['attnotnull']=False
            if fault=='precision' and model is CustomerExcelSourceRow:
                next(r for r in rows if r['attname']=='demand_mw')['type']='numeric(29,6)'
            return rows
        if 'FROM pg_constraint' in sql:
            if model is s.Interaction:return []
            rows=[{'conname':c.name,'contype':'c','convalidated':True,'definition':'CHECK (...)'} for c in model._meta.constraints]
            rows += [{'conname':'pk','contype':'p','convalidated':True,'definition':'PRIMARY KEY (id)'},
                {'conname':'u','contype':'u','convalidated':True,'definition':'UNIQUE (tenant_id, source_sha256, source_sheet, source_row'+(', source_column)' if model is CustomerSourceNote else ')')}]
            if fault=='unique':rows[-1]['definition']='UNIQUE (source_row)'
            return rows
        raise AssertionError(sql)
    monkeypatch.setattr(s,'query',query)
    if fault:
        with pytest.raises(s.ApprovalError):s.verify_schema()
    else:assert len(s.verify_schema())==64


@pytest.mark.parametrize('actual,expected,equal',[
    ('numeric(30,6)','numeric(30, 6)',True),
    ('numeric(29,6)','numeric(30, 6)',False),
    ('numeric(30,5)','numeric(30, 6)',False),
    ('character varying(255)','varchar(255)',True),
])
def test_postgres_type_formatting_preserves_precision_and_scale(actual,expected,equal):
    assert (s.catalog_type(actual)==s.catalog_type(expected)) is equal
