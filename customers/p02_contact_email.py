"""One approved, tenant-specific P02 contact email completion; no general importer."""
from datetime import datetime, timezone as dt_timezone
import hashlib
import json
from pathlib import Path
import subprocess

from django.conf import settings
from django.db import connection, transaction

from auditlog.models import AuditLog
from auditlog.services import record_audit_event
from customers.models import BillingAccount, CustomerContact, IndustrialUnit, TenantCustomer
from tenants.models import Tenant

HEAD = '7da6040231b00e2aea575e39e9e82e363413f4cf'
OPERATION = 'P02_CONTACT_167_EMAIL_ROW168_V1'
SOURCE_SHA256 = '3855ca94224f86ba6f541f656f96edc7e35936f5aa7468945658a364284abdec'
REPORT_SHA256 = 'a3c126f6b8fd882f52a04abc3bfe98e6920bdfabeac7861f6d2f098962c1a63b'
NEW_EMAIL = 'redacted@example.invalid'
APPROVED = {
    'tenant_id': 1, 'tenant_name': 'هما نیرو', 'customer_id': 0,
    'industrial_unit_id': 0, 'official_name': 'مشتری نمونه',
    'billing_account_id': 0, 'bill_id': 'EXAMPLE-BILL-ID', 'contact_id': 0,
    'customer_status': 'LEAD',
    'contact': {'contact_person': 'شخص نمونه', 'mobile_number': '09000000000',
                'phone_number': '000-00000000', 'website': None,
                'is_primary': False, 'data_quality_status': 'REVIEW_REQUIRED'},
}
MANIFEST = 'docs/P02_EMAIL_MANIFEST.json'
PG_RESTORE = Path(r'D:\Program Files\PostgreSQL\17\bin\pg_restore.exe')


class ApprovalError(RuntimeError):
    pass


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _digest(value):
    return hashlib.sha256(json.dumps(_jsonable(value), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _image(instance):
    return _jsonable({f.attname: getattr(instance, f.attname) for f in instance._meta.local_fields})


def endpoint_fingerprint():
    cfg = connection.settings_dict
    return _digest({k: str(cfg.get(k) or '') for k in ('HOST', 'PORT', 'NAME', 'USER')})


def verify_runtime_source():
    raise ApprovalError('Historical one-time operation disabled in sanitized GitHub copy.')
    root = Path(settings.BASE_DIR).resolve()
    path = root / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ApprovalError('Source manifest missing or unsafe.')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('reference_head') != HEAD or manifest.get('operation') != OPERATION:
        raise ApprovalError('Unexpected source manifest.')
    for rel, expected in {**manifest['protected'], **manifest['installed_hashes']}.items():
        p = root / rel
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):
            raise ApprovalError('Missing or unsafe reviewed source: ' + rel)
        if hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ApprovalError('Source changed: ' + rel)
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
        if result.returncode:
            raise ApprovalError('Git verification failed.')
        return result.stdout.decode('utf-8', 'strict')
    if git('rev-parse', 'HEAD').strip() != HEAD:
        raise ApprovalError('HEAD changed; review current source before running this one-time operation.')
    allowed = {"?? '", '?? templates.zip'}
    for rel in [*manifest['installed_hashes'], MANIFEST]:
        allowed.update({'?? ' + rel, 'A  ' + rel})
    if any(e and e not in allowed for e in git('status', '--porcelain=v1', '-z', '--untracked-files=all').split('\0')):
        raise ApprovalError('Unexpected source changes; send git status --short.')
    if connection.vendor != 'postgresql' or str(connection.settings_dict.get('NAME')) != 'homaniroo_dev':
        raise ApprovalError('Only the existing PostgreSQL homaniroo_dev is accepted.')
    return root


def _records(*, lock=False):
    def qs(model):
        return model.objects.select_for_update() if lock else model.objects.all()
    a = APPROVED
    try:
        tenant = qs(Tenant).get(pk=a['tenant_id'], status='ACTIVE')
        customer = qs(TenantCustomer).get(pk=a['customer_id'], tenant_id=a['tenant_id'],
                                          industrial_unit_id=a['industrial_unit_id'])
        billing = qs(BillingAccount).get(pk=a['billing_account_id'], tenant_id=a['tenant_id'],
                                         tenant_customer_id=a['customer_id'], bill_id=a['bill_id'])
        contact = qs(CustomerContact).get(pk=a['contact_id'], tenant_id=a['tenant_id'],
                                          tenant_customer_id=a['customer_id'])
        unit = IndustrialUnit.objects.get(pk=a['industrial_unit_id'])
    except (Tenant.DoesNotExist, TenantCustomer.DoesNotExist, BillingAccount.DoesNotExist,
            CustomerContact.DoesNotExist, IndustrialUnit.DoesNotExist) as error:
        raise ApprovalError('Approved identifiers or company links no longer match. No overwrite allowed.') from error
    if tenant.display_name != a['tenant_name'] or unit.official_name != a['official_name']:
        raise ApprovalError('Company or industrial identity changed; review required.')
    if customer.customer_status != a['customer_status']:
        raise ApprovalError('Customer status changed; this operation cannot change it back.')
    if any(getattr(contact, key) != value for key, value in a['contact'].items()):
        raise ApprovalError('Contact details changed since approved preview. No overwrite allowed.')
    events = AuditLog.objects.filter(tenant_id=a['tenant_id'], entity_type='CUSTOMER_CONTACT',
        entity_id=str(a['contact_id']), metadata__operation=OPERATION)
    count = events.count()
    if count > 1:
        raise ApprovalError('Multiple operation audit events found; no automatic repair.')
    if count:
        event = events.get()
        if not event.success or event.metadata.get('source_sha256') != SOURCE_SHA256:
            raise ApprovalError('Operation audit evidence differs; review required.')
    if contact.email == NEW_EMAIL:
        state = 'ALREADY_APPLIED' if count else 'ALREADY_PRESENT_NO_WRITE'
    elif contact.email is None and not count:
        state = 'READY'
    else:
        raise ApprovalError('Email or operation history differs from approved empty value; do not overwrite.')
    images = {'tenant': _image(tenant), 'customer': _image(customer), 'billing': _image(billing),
              'contact': _image(contact), 'industrial_unit': _image(unit)}
    return tenant, contact, state, images


def inspect_target():
    if connection.in_atomic_block:
        raise ApprovalError('Preflight must own its read-only transaction.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '30s'")
        _, _, state, images = _records()
        result = {'state': state, 'snapshot_sha256': _digest(images),
                  'endpoint_fingerprint': endpoint_fingerprint(),
                  'operation': OPERATION, 'database': 'homaniroo_dev',
                  'tenant_id': APPROVED['tenant_id'], 'customer_id': APPROVED['customer_id'],
                  'contact_id': APPROVED['contact_id'], 'field': 'email', 'new_value': NEW_EMAIL}
        transaction.set_rollback(True)
    return result


def validate_backup_receipt(path, root, snapshot):
    supplied = Path(path)
    if supplied.is_symlink() or not supplied.is_file():
        raise ApprovalError('Fresh backup manifest missing or unsafe.')
    supplied = supplied.resolve()
    if supplied.is_relative_to(root):
        raise ApprovalError('Backup and manifest must remain outside project.')
    receipt = json.loads(supplied.read_text(encoding='utf-8'))
    expected = {'operation': OPERATION, 'source_head': HEAD, 'source_sha256': SOURCE_SHA256,
                'database': 'homaniroo_dev', 'endpoint_fingerprint': endpoint_fingerprint(),
                'before_snapshot_sha256': snapshot, 'archive_list_verified': True}
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise ApprovalError('Backup does not match this operation, database or current target snapshot.')
    stamp = datetime.fromisoformat(receipt['created_at_utc'])
    if stamp.tzinfo is None or not -60 <= (datetime.now(dt_timezone.utc) - stamp).total_seconds() <= 900:
        raise ApprovalError('Backup receipt is stale or timestamp invalid. Create a new verified backup.')
    dump = Path(receipt['backup_path'])
    if dump.is_symlink() or not dump.is_file():
        raise ApprovalError('Backup archive missing or unsafe.')
    dump = dump.resolve()
    if dump.is_relative_to(root) or dump.parent != supplied.parent:
        raise ApprovalError('Backup archive path is not the expected external backup directory.')
    if dump.stat().st_size != receipt.get('size_bytes') or dump.stat().st_size <= 5:
        raise ApprovalError('Backup size verification failed.')
    with dump.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ApprovalError('Backup is not a PostgreSQL custom archive.')
    if sha_file(dump) != receipt.get('sha256'):
        raise ApprovalError('Backup checksum differs; do not proceed.')
    restore = PG_RESTORE
    if not restore.is_file():
        raise ApprovalError('Confirmed PostgreSQL 17 pg_restore tool not found.')
    check = subprocess.run([str(restore), '--list', str(dump)], capture_output=True, timeout=60)
    if check.returncode or not check.stdout.strip():
        raise ApprovalError('Backup archive list verification failed. No restore was attempted.')
    return receipt


@transaction.atomic
def _apply_locked(expected_snapshot):
    tenant, contact, state, images = _records(lock=True)
    if state != 'READY':
        return {'state': state, 'rows_updated': 0}
    if _digest(images) != expected_snapshot:
        raise ApprovalError('Target changed after backup; no blind retry or overwrite.')
    contact.email = NEW_EMAIL
    contact.save(update_fields=['email'])
    contact.refresh_from_db()
    expected_contact = dict(images['contact'], email=NEW_EMAIL)
    if _image(contact) != expected_contact:
        raise ApprovalError('Unexpected contact field changed; transaction rolled back.')
    # Recheck the other approved entities, without changing any of their fields.
    _, _, _, after_images = _records_after_email()
    if any(after_images[k] != images[k] for k in images if k != 'contact'):
        raise ApprovalError('Unexpected related record change; transaction rolled back.')
    event = record_audit_event(action='CREATE_OR_UPDATE', tenant=tenant,
        entity_type='CUSTOMER_CONTACT', entity_id=contact.pk,
        description='ایمیل خالی اطلاعات تماس از منبع تأییدشده P02 تکمیل شد.', severity='IMPORTANT',
        metadata={'operation': OPERATION, 'field': 'email', 'source_sha256': SOURCE_SHA256,
                  'source_filename': 'آذربایجان (1).xlsx', 'source_sheet': 'Sheet1',
                  'source_excel_row': 168, 'source_column': 'J', 'review_report_sha256': REPORT_SHA256,
                  'customer_id': APPROVED['customer_id'], 'executed_via': 'management_command',
                  'actor_is_not_attributed_to_a_django_user': True})
    if event is None:
        raise ApprovalError('Audit could not be stored; email update rolled back.')
    return {'state': 'APPLIED', 'rows_updated': 1, 'audit_id': event.pk}


def _records_after_email():
    # A filled approved email with no audit yet is a valid intermediate state.
    return _records()


def apply_approved_email(backup_manifest):
    root = verify_runtime_source()
    preview = inspect_target()
    if preview['state'] != 'READY':
        return {'state': preview['state'], 'rows_updated': 0}
    validate_backup_receipt(backup_manifest, root, preview['snapshot_sha256'])
    # Durable atomic ensures this call cannot report an uncommitted outer transaction.
    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
        result = _apply_locked(preview['snapshot_sha256'])
    # If commit status is uncertain, the command must stop; inspect state before retry.
    verified = inspect_target()
    if verified['state'] not in {'ALREADY_APPLIED', 'ALREADY_PRESENT_NO_WRITE'}:
        raise ApprovalError('Post-commit verification uncertain. Inspect state; do not retry blindly.')
    return result
