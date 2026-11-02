"""P02 complete Excel preservation and explicit correction preview; no write mode yet."""
from collections import Counter
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import connection,transaction
from django.db.migrations.executor import MigrationExecutor

from customers.models import IndustrialUnit,TenantCustomer,CustomerContact,BillingAccount,CustomerSourceNote,CustomerExcelSourceRow
from customers.p02_contact_email import ApprovalError,_digest,_image,sha_file,endpoint_fingerprint
from customers.services import normalize_company_name
from tenants.models import Tenant

HEAD='481eb679035880b91068fc995f642968e82c0382'
SOURCE_SHA='3855ca94224f86ba6f541f656f96edc7e35936f5aa7468945658a364284abdec'
ORIGINAL_REMAINING_REPORT_SHA='54e422c5adec276c1517a7c68fd0a175e675981c5aad8223891d759492d33822'
OPERATION='P02_COMPLETE_EXCEL_424_V1'
MANIFEST='docs/P02_WORKBOOK_MANIFEST.json'
ENDPOINT='66b71389a490c01cc55627b0f125bec0b018bfc49b2f397012939ec4044842b3'
LABELS={'A':'شرکت برق منطقه‌ای','B':'شناسه قبض','C':'نام مشترک','D':'مدیر انرژی','E':'موبایل','F':'شماره تماس',
        'G':'ستون G؛ عنوان در فایل مشخص نیست','H':'دیماند قراردادی؛ مگاوات (MW)','I':'فکس','J':'ایمیل/واتس‌اپ',
        'K':'ستون K؛ معنی مقدار حدس زده نشده','L':'یادداشت منبع؛ تاریخ وقوع مشخص نیست','M':'ستون M؛ بدون عنوان'}


def canonical_text(value):
    return re.sub(r'\s+',' ',str('' if value is None else value).translate(str.maketrans('يك','یک'))).strip()


def source_string(value):
    """No phone-prefix, date or unit inference. Raw typed cells are stored separately."""
    if value is None:return ''
    if isinstance(value,bool):raise ApprovalError('Unexpected boolean in a text/phone source column.')
    if isinstance(value,float):
        number=Decimal(str(value))
        return format(number,'f').rstrip('0').rstrip('.') if '.' in format(number,'f') else format(number,'f')
    return str(value).strip()


def demand_value(value):
    if value is None or str(value).strip()=='':return None
    try:result=Decimal(str(value).strip())
    except InvalidOperation:raise ApprovalError('Non-numeric source demand; no guessed conversion.') from None
    if not result.is_finite() or result<0 or result.as_tuple().exponent < -6 or result.adjusted()>23:
        raise ApprovalError('Demand cannot be represented exactly in the reviewed Decimal field.')
    return result


def workbook_rows_for_customer(tenant,customer):
    if customer.tenant_id!=tenant.pk:raise PermissionDenied('پرونده متعلق به شرکت فعال نیست.')
    rows=list(CustomerExcelSourceRow.objects.for_tenant(tenant).filter(tenant_customer_id=customer.pk))
    for row in rows:
        row.display_cells=[{'column':col,'label':LABELS[col],'value':row.raw_cells.get(col)} for col in LABELS]
    return rows


def load_source(report_path,excel_path):
    root=Path(settings.BASE_DIR).resolve()
    for path,expected in ((Path(report_path),ORIGINAL_REMAINING_REPORT_SHA),(Path(excel_path),SOURCE_SHA)):
        if path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(root) or sha_file(path)!=expected:
            raise ApprovalError('Original workbook or reviewed report missing, changed or inside project.')
    report=json.loads(Path(report_path).read_text(encoding='utf-8'))
    if len(report['rows'])!=424 or report['source_file']['sha256']!=SOURCE_SHA:
        raise ApprovalError('Unexpected complete source workbook.')
    return report


def verify_runtime_source():
    root=Path(settings.BASE_DIR).resolve();manifest_path=root/MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():raise ApprovalError('Source manifest missing or unsafe.')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['reference_head']!=HEAD or manifest['operation']!=OPERATION:raise ApprovalError('Unexpected source manifest.')
    for rel,expected in {**manifest['protected'],**manifest['installed_hashes']}.items():
        path=root/rel
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):raise ApprovalError('Unsafe source: '+rel)
        if hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=expected:raise ApprovalError('Source differs: '+rel)
    def git(*args):
        r=subprocess.run(['git','-C',str(root),*args],capture_output=True)
        if r.returncode:raise ApprovalError('Git verification failed.')
        return r.stdout.decode('utf-8','strict')
    if git('rev-parse','HEAD').strip()!=HEAD:raise ApprovalError('HEAD differs; do not bypass source guard.')
    allowed={"?? '",'?? templates.zip'}
    for rel in [*manifest['installed_hashes'],MANIFEST]:allowed.update({'?? '+rel,' M '+rel,'M  '+rel,'A  '+rel})
    if any(e and e not in allowed for e in git('status','--porcelain=v1','-z','--untracked-files=all').split('\0')):
        raise ApprovalError('Unexpected Git changes; send git status --short.')
    if connection.vendor!='postgresql' or str(connection.settings_dict.get('NAME'))!='homaniroo_dev' or endpoint_fingerprint()!=ENDPOINT:
        raise ApprovalError('Only the reviewed homaniroo_dev endpoint is accepted.')
    return root


def choose_contact(row,candidates):
    if len(candidates)==1:return candidates[0]
    def score(c):
        return sum(bool(source_string(row['source_cells'].get(col))) and canonical_text(source_string(row['source_cells'][col]))==canonical_text(c.get(key))
                   for col,key in [('D','contact_person'),('E','mobile_number'),('F','phone_number')])
    scored=[(score(c),c) for c in candidates]
    best=max((n for n,c in scored),default=0)
    chosen=[c for n,c in scored if n==best]
    if best==0 or len(chosen)!=1:raise ApprovalError('Multiple contact records require a unique source-field match; no new contact guessed.')
    return chosen[0]


def build_plan(source,state):
    """Pure deterministic preview; data in source/state stays outside Git."""
    customers={c['tenant_customer_id']:c for c in state['customers']}
    units={u['industrial_unit_id']:u for u in state['units']}
    accounts={a['billing_account_id']:a for a in state['accounts']}
    contacts={c['contact_id']:c for c in state['contacts']}
    desired={};records=[];note_inserts=[];assigned_contacts=set();desired_names={}
    def change(kind,pk,fields):
        key=(kind,pk);entry=desired.setdefault(key,{})
        for field,value in fields.items():
            if field in entry and entry[field]!=value:raise ApprovalError('Conflicting source values for one target field; do not choose the last row.')
            entry[field]=value
    for row in source['rows']:
        n=row['excel_row'];cells=row['source_cells'];bills=row['existing_bill_records_in_target']
        if n==14:
            customer_id=594;account_id=None;contact_id=13;expected_contact_owner=594;expected_unit_id=589
            if bills or cells.get('B') not in (None,''):raise ApprovalError('Missing-bill source row changed.')
            if customer_id not in customers or contact_id not in contacts:raise ApprovalError('Reviewed no-bill source contact missing.')
            existing=contacts[contact_id]
            if existing['tenant_customer_id']!=customer_id or source_string(cells['E'])!=source_string(existing['mobile_number']):
                raise ApprovalError('No-bill row lacks the reviewed exact phone and customer link.')
            if canonical_text(cells['C'])!=canonical_text(units[customers[customer_id]['industrial_unit_id']]['official_name']):
                raise ApprovalError('No-bill row name changed; name alone cannot link a customer.')
        else:
            if len(bills)!=1:raise ApprovalError('Source row must have one reviewed existing bill, never a name-only link.')
            ref=bills[0];account_id=ref['billing_account_id']
            if account_id not in accounts:raise ApprovalError('Reviewed account missing.')
            account=accounts[account_id]
            if account['tenant_id']!=1 or account['bill_id']!=ref['bill_id'] or account['tenant_customer_id']!=ref['tenant_customer_id']:
                raise ApprovalError('Bill ownership or identifier changed since review.')
            customer_id=account['tenant_customer_id'];expected_contact_owner=customer_id
            original_candidate=next(c for c in row['target_company_candidates'] if c['tenant_customer_id']==customer_id)
            expected_unit_id=original_candidate['industrial_unit_id']
            candidates=[contacts[c['contact_id']] for c in next(c for c in row['target_company_candidates'] if c['tenant_customer_id']==customer_id)['existing_contacts'] if c['contact_id'] in contacts]
            contact_id=choose_contact(row,candidates)['contact_id']
            if n==122:
                if (account_id,customer_id,contact_id)!=(144,700,121) or 701 not in customers:raise ApprovalError('Reviewed row-122 correction differs.')
                customer_id=701;expected_unit_id=696
                change('BillingAccount',account_id,{'tenant_customer_id':701})
                change('CustomerContact',contact_id,{'tenant_customer_id':701})
            regional=canonical_text(cells.get('A'))
            if regional:change('BillingAccount',account_id,{'regional_company':regional})
        customer=customers[customer_id]
        if customer['tenant_id']!=1 or customer['industrial_unit_id']!=expected_unit_id:raise ApprovalError('Customer company/industrial identity changed since review.')
        unit_id=customer['industrial_unit_id'];unit=units[unit_id];name=canonical_text(cells.get('C'))
        if name:
            if unit_id in desired_names and desired_names[unit_id]!=name:raise ApprovalError('Multiple source names point to one identity; review required.')
            desired_names[unit_id]=name
            if name!=unit['official_name']:
                if any(m['industrial_unit_id']==unit_id and m['tenant_id']!=1 for m in state['other_memberships']):
                    raise ApprovalError('A shared industrial name would affect another company; no global overwrite.')
                change('IndustrialUnit',unit_id,{'official_name':name,'normalized_name':normalize_company_name(name)})
        contact=contacts[contact_id]
        if contact['tenant_id']!=1 or contact['tenant_customer_id']!=expected_contact_owner:raise ApprovalError('Contact company/customer binding changed since review.')
        for col,field in [('D','contact_person'),('E','mobile_number'),('F','phone_number')]:
            value=canonical_text(cells[col]) if col=='D' else source_string(cells[col])
            if value:change('CustomerContact',contact_id,{field:value})
        email=source_string(cells.get('J'))
        if email:
            try:validate_email(email)
            except ValidationError:pass  # mixed text, WhatsApp and fax remain intact in raw cells.
            else:change('CustomerContact',contact_id,{'email':email})
        assigned_contacts.add(contact_id)
        record={'tenant_customer_id':customer_id,'billing_account_id':account_id,'source_row':n,
            'raw_cells':cells,'source_headers':source['source_file']['headers'],'cell_types':row['source_cell_types'],'demand_mw':demand_value(cells.get('H'))}
        records.append(record)
        note=cells.get('L')
        if note is not None and str(note).strip():
            existing=[x for x in state['notes'] if x['source_row']==n]
            if existing:
                if len(existing)!=1 or existing[0]['tenant_customer_id']!=customer_id or existing[0]['body']!=note:
                    raise ApprovalError('Existing imported note differs; preserve it and stop.')
            else:
                already=any(i['tenant_customer_id']==customer_id and canonical_text(note) in (canonical_text(i.get('notes')),canonical_text(i.get('interaction_status'))) for i in state['interactions'])
                if not already:note_inserts.append({'source_row':n,'tenant_customer_id':customer_id,'billing_account_id':account_id,'body':note})
    if len(records)!=424 or len({r['source_row'] for r in records})!=424:raise ApprovalError('Incomplete or duplicated workbook rows.')
    if sum(r['demand_mw'] is not None for r in records)!=423:raise ApprovalError('Expected 423 exact demand values.')
    images={'IndustrialUnit':units,'CustomerContact':contacts,'BillingAccount':accounts}
    updates=[]
    for (kind,pk),fields in sorted(desired.items()):
        before={k:images[kind][pk][k] for k in fields if images[kind][pk][k]!=fields[k]}
        if before:updates.append({'model':kind,'pk':pk,'before':before,'after':{k:fields[k] for k in before}})
    return {'operation':OPERATION,'source_rows':records,'updates':updates,'new_notes':note_inserts,
        'summary':{'source_rows_to_preserve':424,'demand_values_mw':423,'new_source_notes':len(note_inserts),
            'records_to_update':dict(Counter(x['model'] for x in updates)),'new_customers':0,'new_contacts':0,
            'new_interactions':0,'status_changes':0,'ownership_changes':0,'other_company_changes':0,
            'customers_deleted_or_merged':0,'missing_bill_rows_preserved_without_inventing_bill':1}}


def read_state(source):
    tenant=Tenant.objects.get(pk=1,status='ACTIVE',display_name='هما نیرو')
    ids={a['tenant_customer_id'] for row in source['rows'] for a in row['existing_bill_records_in_target']}|{594,701}
    customers=list(TenantCustomer.objects.filter(tenant_id=1,pk__in=sorted(ids)).order_by('tenant_customer_id').values())
    if len(customers)!=len(ids):raise ApprovalError('A reviewed customer disappeared.')
    unit_ids={c['industrial_unit_id'] for c in customers}
    from interactions.models import Interaction
    state={'tenant':_image(tenant),'customers':customers,
        'units':list(IndustrialUnit.objects.filter(pk__in=sorted(unit_ids)).order_by('industrial_unit_id').values()),
        'other_memberships':list(TenantCustomer.objects.filter(industrial_unit_id__in=sorted(unit_ids)).exclude(tenant_id=1).order_by('tenant_customer_id').values('tenant_id','industrial_unit_id','tenant_customer_id')),
        'accounts':list(BillingAccount.objects.filter(tenant_id=1,tenant_customer_id__in=sorted(ids)).order_by('billing_account_id').values()),
        'contacts':list(CustomerContact.objects.filter(tenant_id=1,tenant_customer_id__in=sorted(ids)).order_by('contact_id').values()),
        'notes':list(CustomerSourceNote.objects.filter(tenant_id=1,source_sha256=SOURCE_SHA,source_sheet='Sheet1',source_column='L').order_by('source_row').values()),
        'interactions':list(Interaction.objects.filter(tenant_id=1,tenant_customer_id__in=sorted(ids)).order_by('interaction_id').values())}
    return state


def inspect_plan(source):
    if connection.in_atomic_block:raise ApprovalError('Preflight must own its read-only transaction.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '30s'")
        executor=MigrationExecutor(connection);executor.loader.check_consistent_history(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):raise ApprovalError('Pending migrations; do not execute them automatically.')
        state=read_state(source)
        if CustomerExcelSourceRow.objects.filter(tenant_id=1,source_sha256=SOURCE_SHA,source_sheet='Sheet1').exists():
            raise ApprovalError('Workbook preservation already exists; re-review instead of duplicating or overwriting.')
        plan=build_plan(source,state)
        plan.update(state='READY_FOR_REVIEW',database='homaniroo_dev',tenant_id=1,snapshot_sha256=_digest(state),
            endpoint_fingerprint=endpoint_fingerprint(),source_sha256=SOURCE_SHA,transaction_read_only_verified=True,
            database_changed=False,contains_private_source_data=True)
        transaction.set_rollback(True)
    plan['transaction_rolled_back']=True
    return plan
