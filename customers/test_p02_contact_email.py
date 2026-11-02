"""Isolated test database only. No application-data writes or live backups."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from auditlog.models import AuditLog
from customers import p02_contact_email as service
from customers.models import BillingAccount, CustomerContact, IndustrialUnit, TenantCustomer
from tenants.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def records(transactional_db, monkeypatch):
    models = [Tenant, IndustrialUnit, TenantCustomer, BillingAccount, CustomerContact]
    created = []
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA IF NOT EXISTS core')
    for model in models:
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT to_regclass(%s)', [model._meta.db_table])
                exists = cursor.fetchone()[0] is not None
        else:
            exists = model._meta.db_table in connection.introspection.table_names()
        if not exists:
            with connection.schema_editor() as editor:
                editor.create_model(model)
            created.append(model)
    tag = uuid4().hex[:16]
    tenant = Tenant.objects.create(tenant_code='P02_' + tag, legal_name='هما نیرو', display_name='هما نیرو')
    other = Tenant.objects.create(tenant_code='P02_OTHER_' + tag, legal_name='Other', display_name='Other')
    unit = IndustrialUnit.objects.create(official_name=service.APPROVED['official_name'], normalized_name='p02_' + tag)
    customer = TenantCustomer.objects.create(tenant_id=tenant.pk, industrial_unit=unit, customer_status='LEAD')
    other_customer = TenantCustomer.objects.create(tenant_id=other.pk, industrial_unit=unit, customer_status='LEAD')
    account = BillingAccount.objects.create(tenant_id=tenant.pk, tenant_customer=customer, bill_id=service.APPROVED['bill_id'])
    contact = CustomerContact.objects.create(tenant_id=tenant.pk, tenant_customer=customer, email=None, **service.APPROVED['contact'])
    other_contact = CustomerContact.objects.create(tenant_id=other.pk, tenant_customer=other_customer, email='other@example.com')
    approved = deepcopy(service.APPROVED)
    approved.update(tenant_id=tenant.pk, customer_id=customer.pk, industrial_unit_id=unit.pk,
                    billing_account_id=account.pk, contact_id=contact.pk)
    monkeypatch.setattr(service, 'APPROVED', approved)
    yield N(tenant=tenant, other=other, unit=unit, customer=customer, account=account,
            contact=contact, other_contact=other_contact, other_customer=other_customer)
    ids = [tenant.pk, other.pk]
    AuditLog.objects.filter(tenant_id__in=ids)._raw_delete('default')
    CustomerContact.objects.filter(pk__in=[contact.pk, other_contact.pk])._raw_delete('default')
    BillingAccount.objects.filter(pk=account.pk)._raw_delete('default')
    TenantCustomer.objects.filter(pk__in=[customer.pk, other_customer.pk])._raw_delete('default')
    IndustrialUnit.objects.filter(pk=unit.pk)._raw_delete('default')
    Tenant.objects.filter(pk__in=ids)._raw_delete('default')
    for model in reversed(created):
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def snapshot():
    return {m._meta.label: list(m.objects.order_by(m._meta.pk.name).values()) for m in
            [Tenant, IndustrialUnit, TenantCustomer, BillingAccount, CustomerContact, AuditLog]}


def apply():
    _, _, _, images = service._records()
    return service._apply_locked(service._digest(images))


def test_apply_changes_only_one_email_and_records_source_atomically(records):
    before = snapshot()
    result = apply()
    after = snapshot()
    assert result['state'] == 'APPLIED' and result['rows_updated'] == 1
    contacts_before = before.pop('customers.CustomerContact')
    contacts_after = after.pop('customers.CustomerContact')
    expected = deepcopy(contacts_before)
    next(c for c in expected if c['contact_id'] == records.contact.pk)['email'] = service.NEW_EMAIL
    assert contacts_after == expected
    before.pop('auditlog.AuditLog'); events = after.pop('auditlog.AuditLog')
    assert before == after and len(events) == 1
    event = AuditLog.objects.get(pk=result['audit_id'])
    assert event.metadata['source_excel_row'] == 168 and event.metadata['source_column'] == 'J'
    assert event.metadata['source_sha256'] == service.SOURCE_SHA256
    assert event.entity_id == str(records.contact.pk) and event.tenant_id == records.tenant.pk
    assert event.auth_user_id is None and event.username == ''


def test_retry_is_noop_with_one_audit(records):
    apply(); before = snapshot()
    result = apply()
    assert result == {'state': 'ALREADY_APPLIED', 'rows_updated': 0}
    assert snapshot() == before


def test_already_present_without_our_audit_does_not_forge_history(records):
    CustomerContact.objects.filter(pk=records.contact.pk).update(email=service.NEW_EMAIL)
    before = snapshot()
    assert apply() == {'state': 'ALREADY_PRESENT_NO_WRITE', 'rows_updated': 0}
    assert snapshot() == before


@pytest.mark.parametrize('value', ['different@example.com', ''])
def test_non_null_email_not_overwritten(records, value):
    CustomerContact.objects.filter(pk=records.contact.pk).update(email=value)
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


@pytest.mark.parametrize('field,value', [('mobile_number', '09120000000'), ('contact_person', 'Another person'),
                                         ('is_primary', True), ('website', 'https://example.com')])
def test_changed_contact_snapshot_blocks_overwrite(records, field, value):
    CustomerContact.objects.filter(pk=records.contact.pk).update(**{field: value})
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


def test_other_company_contact_cannot_be_updated(records, monkeypatch):
    monkeypatch.setitem(service.APPROVED, 'contact_id', records.other_contact.pk)
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


def test_customer_status_is_never_reset_to_lead(records):
    TenantCustomer.objects.filter(pk=records.customer.pk).update(customer_status='ACTIVE')
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


def test_changed_bill_link_blocks_operation(records):
    BillingAccount.objects.filter(pk=records.account.pk).update(bill_id='different')
    with pytest.raises(service.ApprovalError):
        apply()
    records.contact.refresh_from_db()
    assert records.contact.email is None


def test_audit_failure_rolls_back_email(records):
    before = snapshot()
    with patch.object(service, 'record_audit_event', return_value=None), pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


def test_post_backup_snapshot_change_blocks_operation(records):
    _, _, _, image = service._records()
    BillingAccount.objects.filter(pk=records.account.pk).update(regional_company='Changed after backup')
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        service._apply_locked(service._digest(image))
    assert snapshot() == before


def test_audit_and_email_inconsistent_state_is_not_repaired(records):
    apply()
    CustomerContact.objects.filter(pk=records.contact.pk).update(email=None)
    before = snapshot()
    with pytest.raises(service.ApprovalError):
        apply()
    assert snapshot() == before


def test_public_apply_requires_valid_backup_before_writing(records):
    before = snapshot()
    with patch.object(service, 'verify_runtime_source', return_value=Path('/not-project')), \
         patch.object(service, 'inspect_target', return_value={'state': 'READY', 'snapshot_sha256': 'snapshot'}), \
         patch.object(service, 'validate_backup_receipt', side_effect=service.ApprovalError('invalid backup')):
        with pytest.raises(service.ApprovalError):
            service.apply_approved_email('/missing-manifest.json')
    assert snapshot() == before


def test_contact_remains_queryable_in_same_customer_profile(records):
    apply()
    item = CustomerContact.objects.for_tenant(records.tenant).get(tenant_customer=records.customer)
    assert item.pk == records.contact.pk and item.email == service.NEW_EMAIL
    assert not CustomerContact.objects.for_tenant(records.other).filter(pk=item.pk).exists()


@pytest.fixture
def backup_receipt(records, tmp_path, monkeypatch):
    from datetime import datetime, timezone as utc
    import json
    backup = tmp_path / 'outside' / 'backup'
    backup.mkdir(parents=True)
    archive = backup / 'homaniroo_dev.dump'
    archive.write_bytes(b'PGDMP-test-archive')
    restore = tmp_path / 'pg_restore.exe'
    restore.write_bytes(b'test-only-tool')
    monkeypatch.setattr(service, 'PG_RESTORE', restore)
    receipt = {'operation': service.OPERATION, 'source_head': service.HEAD,
        'source_sha256': service.SOURCE_SHA256, 'database': 'homaniroo_dev',
        'endpoint_fingerprint': service.endpoint_fingerprint(), 'before_snapshot_sha256': 'snapshot',
        'archive_list_verified': True, 'created_at_utc': datetime.now(utc.utc).isoformat(),
        'backup_path': str(archive), 'size_bytes': archive.stat().st_size, 'sha256': service.sha_file(archive)}
    path = backup / 'backup_manifest.json'
    path.write_text(json.dumps(receipt))
    return N(receipt=receipt, path=path, archive=archive, root=tmp_path/'project')


def test_fresh_verified_backup_receipt_accepted_without_restore(backup_receipt):
    b = backup_receipt
    with patch.object(service.subprocess, 'run', return_value=N(returncode=0, stdout=b'archive listing')) as run:
        assert service.validate_backup_receipt(b.path, b.root, 'snapshot') == b.receipt
    assert run.call_args.args[0][1] == '--list'


@pytest.mark.parametrize('failure', ['stale', 'checksum', 'endpoint', 'snapshot', 'archive-list'])
def test_invalid_backup_is_rejected(backup_receipt, failure):
    from datetime import datetime, timedelta, timezone as utc
    import json
    b = backup_receipt
    if failure == 'stale':
        b.receipt['created_at_utc'] = (datetime.now(utc.utc) - timedelta(hours=1)).isoformat()
    elif failure == 'checksum':
        b.archive.write_bytes(b'PGDMP-tamper-bytes')
    elif failure == 'endpoint':
        b.receipt['endpoint_fingerprint'] = 'another-database'
    elif failure == 'snapshot':
        b.receipt['before_snapshot_sha256'] = 'another-record'
    b.path.write_text(json.dumps(b.receipt))
    with patch.object(service.subprocess, 'run', return_value=N(returncode=1, stdout=b'')), pytest.raises(service.ApprovalError):
        service.validate_backup_receipt(b.path, b.root, 'snapshot')
