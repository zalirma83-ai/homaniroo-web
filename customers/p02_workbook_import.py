"""One approved workbook batch: default readonly; fresh backup, atomic audit, fail-closed retry."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from pathlib import Path
import subprocess

from django.db import connection,transaction
from django.db.migrations.executor import MigrationExecutor
from customers import p02_workbook as planner
from customers.models import CustomerExcelSourceRow,CustomerSourceNote,CustomerContact,BillingAccount,IndustrialUnit,TenantCustomer,IndustrialUnitAlias
from customers.p02_workbook_schema import EXPECTED_CORE_SCHEMA
from auditlog.models import AuditLog
from auditlog.services import record_audit_event
from interactions.models import Interaction
from tenants.models import Tenant

ApprovalError=planner.ApprovalError
OPERATION=planner.OPERATION
HEAD=planner.HEAD
SOURCE_SHA256=planner.SOURCE_SHA
PREVIEW_SHA='85ff8b54f5e95570e4b75e185360e006929c2bed1658e1233244348386a03bd3'
APPROVED_SNAPSHOT='153d916a9556a7a62ac7eff136e674bfd52461b94397988ce865861705d1f0fc'
TENANT_ID=1
BATCH_TYPE='P02_WORKBOOK_IMPORT'
MODEL_MAP={'BillingAccount':BillingAccount,'CustomerContact':CustomerContact,'IndustrialUnit':IndustrialUnit}
ALLOWED_FIELDS={'BillingAccount':{'tenant_customer_id','regional_company'},'CustomerContact':{'tenant_customer_id','contact_person','mobile_number','phone_number','email'},'IndustrialUnit':{'official_name','normalized_name'}}
PLAN_KEYS=('operation','source_rows','updates','new_notes','summary')


def digest(value):
    def encode(item):
        if isinstance(item,Decimal):return str(item)
        if hasattr(item,'isoformat'):return item.isoformat()
        raise TypeError(type(item).__name__)
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=encode).encode('utf-8')).hexdigest()


def load_approval(path):
    root=Path(planner.settings.BASE_DIR).resolve();path=Path(path)
    if path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(root) or planner.sha_file(path)!=PREVIEW_SHA:
        raise ApprovalError('Exact approved preview missing or changed; do not bypass guard.')
    approved=json.loads(path.read_text(encoding='utf-8'))
    if (approved.get('operation')!=OPERATION or approved.get('snapshot_sha256')!=APPROVED_SNAPSHOT or
        approved.get('endpoint_fingerprint')!=planner.ENDPOINT or approved.get('source_sha256')!=SOURCE_SHA256 or
        approved.get('tenant_id')!=TENANT_ID or approved.get('state')!='READY_FOR_REVIEW' or
        not approved.get('transaction_rolled_back') or not approved.get('transaction_read_only_verified')):
        raise ApprovalError('Approved preview scope differs.')
    plan={k:approved[k] for k in PLAN_KEYS}
    for r in plan['source_rows']:r['demand_mw']=None if r['demand_mw'] is None else Decimal(str(r['demand_mw']))
    return plan


def plan_digest(plan):return digest({k:plan[k] for k in PLAN_KEYS})


def query(sql,params=None):
    with connection.cursor() as cursor:
        if params is None:cursor.execute(sql)
        else:cursor.execute(sql,params)
        return [dict(zip([c[0] for c in cursor.description],r)) for r in cursor.fetchall()]


def catalog_type(value):
    """Formatting only: PostgreSQL numeric(30,6) == Django numeric(30, 6).
    Preserve precision/scale, size, type and timezone; never weaken nullability.
    """
    return re.sub(r'\s*,\s*',',',value.replace('varchar(','character varying('))


def verify_schema():
    """Actual PG17 schema, identities, constraints, triggers, incoming dependencies and migrations."""
    if connection.vendor!='postgresql':raise ApprovalError('PostgreSQL only for production inspection/apply.')
    server=query("SELECT current_database() AS db,current_setting('server_version_num') AS version")[0]
    if server['db']!='homaniroo_dev' or int(server['version'])//10000!=17:raise ApprovalError('Database/version differs.')
    executor=MigrationExecutor(connection);executor.loader.check_consistent_history(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):raise ApprovalError('Pending migrations; do not auto-migrate.')
    schemas=[]
    for expected in EXPECTED_CORE_SCHEMA:
        table=expected['table']
        cols=query('''SELECT a.attname AS column_name,format_type(a.atttypid,a.atttypmod) AS data_type,
            a.attnotnull AS not_null,a.attidentity AS identity_kind,
            CASE WHEN a.attname=%s THEN pg_get_expr(d.adbin,d.adrelid) ELSE NULL END AS pk_default
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''',[expected['pk_column'],table])
        constraints=query('''SELECT conname AS constraint_name,contype AS constraint_type,convalidated AS validated,
            pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname''',[table])
        if cols!=expected['columns'] or constraints!=expected['constraints']:raise ApprovalError('Reviewed core schema/identity/constraints differ: '+expected['model'])
        schemas.append({'table':table,'columns':cols,'constraints':constraints})
    for model in (CustomerSourceNote,CustomerExcelSourceRow,Interaction):
        table=model._meta.db_table
        cols=query('''SELECT attname,format_type(atttypid,atttypmod) AS type,attnotnull,attidentity
            FROM pg_attribute WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped ORDER BY attnum''',[table])
        actual={c['attname']:c for c in cols};fields={f.column:f for f in model._meta.local_fields}
        if (not set(fields).issubset(actual) if model is Interaction else set(actual)!=set(fields)):
            raise ApprovalError('Destination columns differ: '+model._meta.label)
        # Interaction is locked/read, never written. Preserve legacy extra columns/types in the fingerprint.
        if model is not Interaction:
            for name,f in fields.items():
                if catalog_type(actual[name]['type'])!=catalog_type(f.db_type(connection)) or actual[name]['attnotnull']==f.null:
                    raise ApprovalError('Destination field type/nullability differs: '+model._meta.label+'.'+name)
        if model is not Interaction and actual[model._meta.pk.column]['attidentity'] not in ('a','d'):
            raise ApprovalError('Destination ID must be generated by PostgreSQL identity.')
        cons=query('''SELECT conname,contype,convalidated,pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname''',[table])
        if not all(c['convalidated'] for c in cons):raise ApprovalError('Unvalidated destination constraint.')
        if model is not Interaction:
            expected_constraints={c.name for c in model._meta.constraints}
            if not expected_constraints.issubset({c['conname'] for c in cons}):raise ApprovalError('Destination constraint missing.')
            unique='UNIQUE (tenant_id, source_sha256, source_sheet, source_row'+(', source_column)' if model is CustomerSourceNote else ')')
            if not any(c['contype']=='u' and c['definition']==unique for c in cons):raise ApprovalError('Destination uniqueness differs.')
            if not any(c['contype']=='p' and c['definition']=='PRIMARY KEY (id)' for c in cons):raise ApprovalError('Destination PK differs.')
        schemas.append({'table':table,'columns':cols,'constraints':cons})
    tables=[s['table'] for s in schemas]
    for table in tables:
        if query('SELECT tgname FROM pg_trigger WHERE tgrelid=to_regclass(%s) AND NOT tgisinternal',[table]):
            raise ApprovalError('Non-internal business triggers require separate review: '+table)
    # Record both incoming/outgoing FK dependencies in the backup fingerprint.
    dependencies=query('''SELECT conrelid::regclass::text AS from_table,confrelid::regclass::text AS to_table,
        conname,convalidated,pg_get_constraintdef(oid) AS definition FROM pg_constraint
        WHERE contype='f' AND (conrelid IN (SELECT to_regclass(x) FROM unnest(%s::text[]) x)
        OR confrelid IN (SELECT to_regclass(x) FROM unnest(%s::text[]) x)) ORDER BY conrelid,conname''',[tables,tables])
    if not all(c['convalidated'] for c in dependencies):raise ApprovalError('Unvalidated FK dependency.')
    return digest({'schemas':schemas,'dependencies':dependencies})


def scope_rows():
    return CustomerExcelSourceRow.objects.filter(tenant_id=TENANT_ID,source_sha256=SOURCE_SHA256,source_sheet='Sheet1')


def operation_events():return AuditLog.objects.filter(tenant_id=TENANT_ID,metadata__operation=OPERATION)


def row_values(row):
    return {'tenant_id':TENANT_ID,'source_filename':'آذربایجان (1).xlsx','source_sha256':SOURCE_SHA256,'source_sheet':'Sheet1',**row}


def note_values(note):
    return {'tenant_id':TENANT_ID,'source_filename':'آذربایجان (1).xlsx','source_sha256':SOURCE_SHA256,
        'source_sheet':'Sheet1','source_column':'L',**note,'body_sha256':hashlib.sha256(note['body'].encode('utf-8')).hexdigest()}


def expected_events(plan):
    events={}
    for row in plan['source_rows']:events[('ROW',str(row['source_row']))]={'record_sha256':digest(row_values(row))}
    for note in plan['new_notes']:events[('NOTE',str(note['source_row']))]={'record_sha256':digest(note_values(note))}
    for u in plan['updates']:events[('UPDATE',u['model']+':'+str(u['pk']))]={'before_sha256':digest(u['before']),'after_sha256':digest(u['after'])}
    return events


def projected_state(before,plan,new_notes):
    expected=deepcopy(before)
    for u in plan['updates']:
        key,pk={'BillingAccount':('accounts','billing_account_id'),'CustomerContact':('contacts','contact_id'),'IndustrialUnit':('units','industrial_unit_id')}[u['model']]
        matches=[r for r in expected[key] if r[pk]==u['pk']]
        if len(matches)!=1 or any(matches[0][f]!=v for f,v in u['before'].items()):raise ApprovalError('Expected before-image differs.')
        matches[0].update(u['after'])
    expected['notes']=sorted(expected['notes']+new_notes,key=lambda r:r['source_row'])
    return expected


def verify_completed(source,plan,state=None):
    """No write on retry. Partial/different source data or missing audit aborts."""
    rows=list(scope_rows().order_by('source_row'))
    events=list(operation_events())
    if not rows and not events:return False
    if len(rows)!=len(plan['source_rows']):raise ApprovalError('Partial workbook import; no retry or overwrite.')
    for actual,expected in zip(rows,sorted(plan['source_rows'],key=lambda r:r['source_row'])):
        values=row_values(expected)
        if any(getattr(actual,k)!=v for k,v in values.items()):raise ApprovalError('Existing workbook row differs; no overwrite.')
    for n in plan['new_notes']:
        matches=list(CustomerSourceNote.objects.filter(tenant_id=TENANT_ID,source_sha256=SOURCE_SHA256,source_sheet='Sheet1',source_row=n['source_row'],source_column='L'))
        if len(matches)!=1 or any(getattr(matches[0],k)!=v for k,v in note_values(n).items()):raise ApprovalError('Imported note missing or changed.')
    expected=expected_events(plan);batch=[];found={}
    for event in events:
        m=event.metadata
        if not event.success or m.get('source_sha256')!=SOURCE_SHA256 or m.get('approved_preview_sha256')!=PREVIEW_SHA or m.get('plan_sha256')!=plan_digest(plan):raise ApprovalError('Audit provenance differs.')
        if m.get('kind')=='BATCH':
            if event.entity_type!=BATCH_TYPE or event.entity_id!=OPERATION:raise ApprovalError('Batch audit identity differs.')
            batch.append(event);continue
        key=(m.get('kind'),m.get('key'))
        if key in found or key not in expected or any(m.get(k)!=v for k,v in expected[key].items()):raise ApprovalError('Audit missing/duplicate/inconsistent.')
        entity_type={'ROW':'CUSTOMER_EXCEL_SOURCE_ROW','NOTE':'CUSTOMER_SOURCE_NOTE','UPDATE':'P02_CORE_CORRECTION'}[key[0]]
        if event.entity_type!=entity_type:raise ApprovalError('Audit entity type differs.')
        if key[0]=='ROW':entity_id=next(r.pk for r in rows if str(r.source_row)==key[1])
        elif key[0]=='NOTE':entity_id=CustomerSourceNote.objects.get(tenant_id=TENANT_ID,source_sha256=SOURCE_SHA256,source_sheet='Sheet1',source_row=int(key[1]),source_column='L').pk
        else:entity_id=key[1]
        if event.entity_id!=str(entity_id):raise ApprovalError('Audit entity link differs.')
        found[key]=event.pk
    if set(found)!=set(expected) or len(batch)!=1:raise ApprovalError('Incomplete atomic audit evidence.')
    batch=batch[0]
    if batch.metadata.get('before_snapshot_sha256')!=APPROVED_SNAPSHOT:raise ApprovalError('Batch before snapshot differs.')
    if state is None:state=planner.read_state(source)
    if batch.metadata.get('after_snapshot_sha256')!=digest(state):raise ApprovalError('Post-import data changed; manual review required, no overwrite.')
    for u in plan['updates']:
        model=MODEL_MAP[u['model']];obj=model.objects.get(pk=u['pk'])
        if hasattr(obj,'tenant_id') and obj.tenant_id!=TENANT_ID:raise ApprovalError('Correction crosses company.')
        if any(getattr(obj,k)!=v for k,v in u['after'].items()):raise ApprovalError('Core after-image differs.')
    return True


def check_ready(source,plan):
    state=planner.read_state(source)
    if verify_completed(source,plan,state):return state,'ALREADY_APPLIED'
    if digest(state)!=APPROVED_SNAPSHOT:raise ApprovalError('Current data differs from approved preview; no blind retry.')
    current=planner.build_plan(source,state)
    if plan_digest(current)!=plan_digest(plan):raise ApprovalError('Current plan differs from exact approved changes.')
    return state,'READY'


def inspect_import(source,plan):
    if connection.in_atomic_block:raise ApprovalError('Inspection must own transaction.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SELECT current_setting('transaction_read_only')")
            if cursor.fetchone()[0]!='on':raise ApprovalError('Read-only transaction failed.')
        schema=verify_schema();state,status=check_ready(source,plan)
        result={'state':status,'operation':OPERATION,'snapshot_sha256':digest(state),'schema_fingerprint':schema,
            'endpoint_fingerprint':planner.endpoint_fingerprint(),'approved_preview_sha256':PREVIEW_SHA,'plan_sha256':plan_digest(plan)}
        transaction.set_rollback(True)
    return result


def validate_backup(path,root,before):
    supplied=Path(path)
    if supplied.is_symlink() or not supplied.is_file() or supplied.resolve().is_relative_to(root):raise ApprovalError('Fresh external backup manifest required.')
    receipt=json.loads(supplied.read_text(encoding='utf-8'))
    expected={'status':'VERIFIED','operation':OPERATION,'source_head':HEAD,'source_sha256':SOURCE_SHA256,
        'database':'homaniroo_dev','endpoint_fingerprint':planner.ENDPOINT,'schema_fingerprint':before['schema_fingerprint'],
        'approved_preview_sha256':PREVIEW_SHA,'plan_sha256':before['plan_sha256'],
        'before_snapshot_sha256':APPROVED_SNAPSHOT,'after_snapshot_sha256':APPROVED_SNAPSHOT,
        'archive_list_verified':True,'migration_executed':False,'restore_executed':False,'workbook_imported':False,
        'database_changed_by_helper':False,'source_changed':False,'schema_identity_constraints_verified':True}
    if any(receipt.get(k)!=v for k,v in expected.items()):raise ApprovalError('Backup receipt is not for this exact workbook import.')
    stamp=datetime.fromisoformat(receipt['created_at_utc'])
    if stamp.tzinfo is None or not -60<=(datetime.now(timezone.utc)-stamp).total_seconds()<=900:raise ApprovalError('Backup older than 15 minutes; stop for a fresh backup.')
    dump=Path(receipt['backup_path'])
    if dump.is_symlink() or not dump.is_file() or dump.resolve().is_relative_to(root) or dump.resolve().parent!=supplied.resolve().parent or dump.stat().st_size!=receipt.get('size_bytes') or dump.stat().st_size<=5:raise ApprovalError('Backup archive path/size differs.')
    with dump.open('rb') as stream:
        if stream.read(5)!=b'PGDMP':raise ApprovalError('Backup is not custom-format PostgreSQL archive.')
    if planner.sha_file(dump)!=receipt.get('sha256'):raise ApprovalError('Backup checksum differs.')
    restore=Path(r'D:\Program Files\PostgreSQL\17\bin\pg_restore.exe')
    if not restore.is_file():raise ApprovalError('Reviewed pg_restore tool missing.')
    listed=subprocess.run([str(restore),'--list',str(dump)],capture_output=True,timeout=60)
    if listed.returncode or not any(l and not l.startswith(b';') for l in listed.stdout.splitlines()) or hashlib.sha256(listed.stdout).hexdigest()!=receipt.get('archive_list_sha256'):raise ApprovalError('Backup archive list differs. No restore performed.')
    return receipt


def write_audit(tenant,plan,kind,key,entity_type,entity_id,**details):
    metadata={'operation':OPERATION,'source_sha256':SOURCE_SHA256,'approved_preview_sha256':PREVIEW_SHA,
        'plan_sha256':plan_digest(plan),'kind':kind,'key':str(key),'executed_via':'management_command',
        'actor_is_not_attributed_to_a_django_user':True,**details}
    event=record_audit_event(action='CREATE_OR_UPDATE',tenant=tenant,entity_type=entity_type,entity_id=entity_id,
        severity='IMPORTANT',description='ورود کنترل‌شده اطلاعات فایل اکسل؛ بدون ایجاد خرید یا تماس فرضی.',metadata=metadata)
    if event is None or not event.success:raise ApprovalError('Audit failed; entire workbook transaction rolled back.')
    return event


def _write_batch(source,plan,before):
    """Called only after backups and locks; separate for isolated transaction tests."""
    tenant=Tenant.objects.get(pk=TENANT_ID)
    for u in plan['updates']:
        if u['model'] not in MODEL_MAP or not set(u['after']).issubset(ALLOWED_FIELDS[u['model']]) or set(u['before'])!=set(u['after']):raise ApprovalError('Unapproved correction field.')
        model=MODEL_MAP[u['model']];qs=model.objects.filter(pk=u['pk'],**u['before'])
        if u['model']!='IndustrialUnit':qs=qs.filter(tenant_id=TENANT_ID)
        elif TenantCustomer.objects.filter(industrial_unit_id=u['pk']).exclude(tenant_id=TENANT_ID).exists():raise ApprovalError('Shared name would affect another company.')
        if qs.update(**u['after'])!=1:raise ApprovalError('Correction target differs; all changes rolled back.')
        write_audit(tenant,plan,'UPDATE',u['model']+':'+str(u['pk']),'P02_CORE_CORRECTION',u['model']+':'+str(u['pk']),
            before_sha256=digest(u['before']),after_sha256=digest(u['after']))
    created_notes=[]
    for n in plan['new_notes']:
        obj=CustomerSourceNote.objects.create(**note_values(n))
        created_notes.append(dict(CustomerSourceNote.objects.filter(pk=obj.pk).values().get()))
        write_audit(tenant,plan,'NOTE',n['source_row'],'CUSTOMER_SOURCE_NOTE',obj.pk,record_sha256=digest(note_values(n)))
    for r in plan['source_rows']:
        obj=CustomerExcelSourceRow.objects.create(**row_values(r))
        write_audit(tenant,plan,'ROW',r['source_row'],'CUSTOMER_EXCEL_SOURCE_ROW',obj.pk,record_sha256=digest(row_values(r)))
    after=planner.read_state(source)
    if digest(after)!=digest(projected_state(before,plan,created_notes)):raise ApprovalError('Unexpected post-write change; whole transaction rolled back.')
    write_audit(tenant,plan,'BATCH',OPERATION,BATCH_TYPE,OPERATION,
        before_snapshot_sha256=APPROVED_SNAPSHOT,after_snapshot_sha256=digest(after))
    if not verify_completed(source,plan,after):raise ApprovalError('Final source/audit verification failed.')
    return {'state':'APPLIED','source_rows_created':len(plan['source_rows']),'notes_created':len(plan['new_notes']),
        'core_records_updated':len(plan['updates']),'audit_created':len(expected_events(plan))+1,
        'new_customers':0,'new_contacts':0,'new_interactions':0,'status_changes':0,'ownership_changes':0,'other_company_changes':0}


def apply_import(source,plan,backup_manifest):
    root=planner.verify_runtime_source()
    before=inspect_import(source,plan)
    if before['state']=='ALREADY_APPLIED':return {'state':'ALREADY_APPLIED','database_changes':0}
    if not backup_manifest:raise ApprovalError('--backup-manifest required; no import.')
    validate_backup(backup_manifest,root,before)
    if connection.in_atomic_block:raise ApprovalError('Import must own transaction.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'");cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)',[20424,TENANT_ID])
            # Short writer exclusion closes phantom/identity races; reads remain available.
            # Sorted names give this operation one deterministic lock order. No writes outside tenant1.
            tables=sorted({m._meta.db_table for m in (Tenant,TenantCustomer,IndustrialUnit,IndustrialUnitAlias,BillingAccount,CustomerContact,Interaction,CustomerSourceNote,CustomerExcelSourceRow,AuditLog)})
            for table in tables:cursor.execute('LOCK TABLE '+connection.ops.quote_name(table)+' IN SHARE ROW EXCLUSIVE MODE')
        planner.verify_runtime_source()
        if verify_schema()!=before['schema_fingerprint']:raise ApprovalError('Schema changed after backup.')
        state,status=check_ready(source,plan)
        if status=='ALREADY_APPLIED':return {'state':'ALREADY_APPLIED','database_changes':0}
        if digest(state)!=before['snapshot_sha256']:raise ApprovalError('Data changed after backup.')
        validate_backup(backup_manifest,root,before)
        return _write_batch(source,plan,state)
