"""Guarded source-note import tests: isolated test database, never live data."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
import json

import pytest
from django.db import transaction
from auditlog.models import AuditLog
from customers import source_notes as s
from customers.models import CustomerSourceNote,CustomerContact,TenantCustomer,BillingAccount,IndustrialUnit
from interactions.models import Interaction
from customers.test_p02_source_notes import data,records,make_note

pytestmark=pytest.mark.django_db(transaction=True)


def image(data):
    return {m._meta.label:list(m.objects.order_by(m._meta.pk.name).values()) for m in
        (CustomerContact,TenantCustomer,BillingAccount,IndustrialUnit,Interaction)}


def apply(data):
    return s._apply_locked(data.plan,s.inspect_targets(data.plan)['snapshot_sha256'])


def test_atomic_note_plus_audit_and_other_records_unchanged(data):
    before=image(data);r=apply(data)
    assert r['state']=='APPLIED' and r['notes_created']==r['audit_created']==1
    assert image(data)==before
    n=CustomerSourceNote.objects.get(tenant_id=data.tenant.pk)
    a=AuditLog.objects.get(tenant_id=data.tenant.pk,entity_type='CUSTOMER_SOURCE_NOTE')
    assert a.entity_id==str(n.pk) and a.metadata['body_sha256']==n.body_sha256
    assert n.body==data.plan[0]['body'] and n.tenant_customer_id==data.customer.pk
    assert a.metadata['source_excel_row']==200 and a.metadata['source_column']=='L'
    assert 'body' not in a.metadata and n.body not in json.dumps(a.metadata,ensure_ascii=False)
    assert a.auth_user_id is None and a.username==''


def test_successful_retry_has_no_duplicate_note_or_audit(data):
    apply(data);before=(CustomerSourceNote.objects.count(),AuditLog.objects.filter(tenant_id=data.tenant.pk).count())
    r=apply(data)
    assert r['state']=='ALREADY_APPLIED' and r['notes_created']==r['audit_created']==0
    assert before==(CustomerSourceNote.objects.count(),AuditLog.objects.filter(tenant_id=data.tenant.pk).count())


def test_audit_failure_rolls_back_note(data,monkeypatch):
    monkeypatch.setattr(s,'record_audit_event',lambda **kwargs:None)
    with pytest.raises(s.ApprovalError,match='Audit write failed'):apply(data)
    assert not CustomerSourceNote.objects.exists()


def test_corrupted_note_never_overwritten(data):
    apply(data);CustomerSourceNote.objects.filter(tenant_id=data.tenant.pk).update(body='تغییر جدید')
    with pytest.raises(s.ApprovalError,match='differs'):apply(data)
    assert CustomerSourceNote.objects.get(tenant_id=data.tenant.pk).body=='تغییر جدید'


def test_audit_without_note_is_not_silently_retried(data):
    apply(data);CustomerSourceNote.objects.filter(tenant_id=data.tenant.pk)._raw_delete('default')
    with pytest.raises(s.ApprovalError,match='Audit exists without'):apply(data)


def test_existing_note_without_audit_is_not_claimed_as_ours(data):
    make_note(data)
    with pytest.raises(s.ApprovalError,match='audit evidence'):apply(data)


def test_stale_snapshot_blocks_all_writes(data):
    snapshot=s.inspect_targets(data.plan)['snapshot_sha256']
    data.account.consumer_code='changed-after-preview';data.account.save(update_fields=['consumer_code'])
    with pytest.raises(s.ApprovalError,match='Target changed'):s._apply_locked(data.plan,snapshot)
    assert not CustomerSourceNote.objects.exists()


def test_batch_audit_failure_rolls_back_every_note(data,monkeypatch):
    second=dict(data.plan[0],source_row=201,body='متن دوم')
    import hashlib
    second['body_sha256']=hashlib.sha256(second['body'].encode()).hexdigest()
    data.plan.append(second)
    actual=s.record_audit_event;calls=[]
    def sometimes(**kwargs):
        calls.append(1)
        return actual(**kwargs) if len(calls)==1 else None
    monkeypatch.setattr(s,'record_audit_event',sometimes)
    with pytest.raises(s.ApprovalError,match='Audit write failed'):apply(data)
    assert not CustomerSourceNote.objects.exists()
    assert not AuditLog.objects.filter(tenant_id=data.tenant.pk,entity_type='CUSTOMER_SOURCE_NOTE').exists()


def test_other_company_note_does_not_get_reused_or_changed(data):
    other=make_note(data,tenant_id=data.other.pk,tenant_customer_id=data.other_customer.pk)
    before=s._image(other);apply(data);other.refresh_from_db()
    assert s._image(other)==before
    assert CustomerSourceNote.objects.filter(tenant_id=data.tenant.pk).count()==1


def test_partial_batch_does_not_resume_silently(data):
    apply(data)
    data.plan.append(dict(data.plan[0],source_row=201))
    with pytest.raises(s.ApprovalError,match='Partial prior import'):s.inspect_targets(data.plan)
    assert CustomerSourceNote.objects.filter(tenant_id=data.tenant.pk).count()==1


def test_changed_audit_source_blocks_success_claim(data):
    apply(data);a=AuditLog.objects.get(tenant_id=data.tenant.pk,entity_type='CUSTOMER_SOURCE_NOTE')
    a.metadata={**a.metadata,'source_sha256':'wrong'};a.save(update_fields=['metadata'])
    with pytest.raises(s.ApprovalError,match='Audit evidence differs'):s.inspect_targets(data.plan)


def test_public_apply_rejects_any_nonapproved_row_plan(data,monkeypatch):
    monkeypatch.setattr(s,'verify_runtime_source',lambda:Path('/unused'))
    with pytest.raises(s.ApprovalError,match='approved 65-row'):s.apply_plan(data.plan,'receipt.json')


def test_full_65_row_batch_is_atomic_and_idempotent(data):
    import hashlib
    base=data.plan[0]
    data.plan=[dict(base,source_row=row,body='یادداشت آزمایشی ردیف '+str(row),
        body_sha256=hashlib.sha256(('یادداشت آزمایشی ردیف '+str(row)).encode()).hexdigest()) for row in s.APPROVED_ROWS]
    before=image(data);first=apply(data)
    assert first['notes_created']==first['audit_created']==65 and image(data)==before
    assert CustomerSourceNote.objects.filter(tenant_id=data.tenant.pk).count()==65
    assert AuditLog.objects.filter(tenant_id=data.tenant.pk,entity_type='CUSTOMER_SOURCE_NOTE').count()==65
    assert apply(data)['state']=='ALREADY_APPLIED'
    assert AuditLog.objects.filter(tenant_id=data.tenant.pk,entity_type='CUSTOMER_SOURCE_NOTE').count()==65
