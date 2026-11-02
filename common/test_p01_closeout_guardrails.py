"""P01 final status/audit and alert serialization regressions.

Threaded tests run only on PostgreSQL's isolated pytest database, never on the
application database. SQLite passes do not establish PostgreSQL locking behavior.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import close_old_connections, connection, transaction
from django.http import Http404

from auditlog.models import AuditLog
from customers import services
from customers.models import CustomerStatusHistory, IndustrialUnit, TenantCustomer
from customers.tests import prospective_customer_tables  # noqa: F401 (pytest fixture)
from interactions import alert_services, scope_locks
from interactions.models import Alert
from tenants.models import Tenant


def actor():
    return N(pk=None, username='p01_test_actor', is_authenticated=True,
             get_username=lambda: 'p01_test_actor', get_full_name=lambda: 'اپراتور آزمایشی')


@pytest.fixture
def lead(prospective_customer_tables):
    unit = IndustrialUnit.objects.create(official_name='نمونه بسته‌شدن P01', normalized_name='p01-closeout')
    customer = TenantCustomer.objects.create(tenant_id=1, industrial_unit=unit, customer_status='LEAD')
    return N(tenant=N(tenant_id=1), other=N(tenant_id=2), customer=customer, user=actor())


def events(r):
    return AuditLog.objects.filter(tenant_id=r.tenant.tenant_id, entity_type='CUSTOMER',
                                  entity_id=str(r.customer.pk), metadata__operation='customer_status_change')


def history(r):
    return CustomerStatusHistory.objects.filter(tenant_id=r.tenant.tenant_id, tenant_customer_id=r.customer.pk)


@pytest.mark.django_db
def test_conversion_has_one_actor_history_and_one_explicit_audit(lead):
    r = lead
    services.convert_to_active_customer(tenant=r.tenant, customer=r.customer, user=r.user)
    r.customer.refresh_from_db()
    assert r.customer.customer_status == 'ACTIVE'
    item = history(r).get()
    assert (item.previous_status, item.new_status, item.reason_code) == ('LEAD', 'ACTIVE', 'LEAD_CONVERTED')
    assert item.changed_by_name == 'اپراتور آزمایشی'
    event = events(r).get()
    assert event.action == 'STATUS_CHANGE' and event.success
    assert event.username == 'p01_test_actor'
    assert event.metadata['history_id'] == item.pk
    assert set(event.metadata) == {'operation', 'history_id', 'previous_status', 'new_status', 'reason_code'}
    assert services.customer_status_history_rows(r.tenant, r.customer)[0]['reason_label'] == 'تبدیل مشترک بالقوه به فعال'
    assert 'LEAD_CONVERTED' not in services.DORMANT_REASON_LABELS
    with pytest.raises(Http404):
        services.convert_to_active_customer(tenant=r.tenant, customer=r.customer, user=r.user)
    assert history(r).count() == events(r).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize('operation', ['convert', 'dormant', 'restore'])
@pytest.mark.parametrize('failure', ['history', 'audit'])
def test_history_or_audit_failure_rolls_back_entire_status_change(lead, operation, failure):
    r = lead
    if operation == 'restore':
        TenantCustomer.objects.filter(pk=r.customer.pk).update(customer_status='DORMANT')
    previous = TenantCustomer.objects.get(pk=r.customer.pk).customer_status
    if failure == 'history':
        target = patch.object(CustomerStatusHistory.objects, 'create', side_effect=ValidationError('test storage failure'))
    else:
        target = patch.object(services, 'record_audit_event', return_value=None)
    with target, pytest.raises(ValidationError):
        if operation == 'convert':
            services.convert_to_active_customer(tenant=r.tenant, customer=r.customer, user=r.user)
        elif operation == 'dormant':
            services.mark_customer_dormant(tenant=r.tenant, customer_id=r.customer.pk, user=r.user, reason_code='OTHER')
        else:
            services.restore_dormant_customer(tenant=r.tenant, customer_id=r.customer.pk, user=r.user, target_status='ACTIVE')
    r.customer.refresh_from_db()
    assert r.customer.customer_status == previous
    assert not history(r).exists() and not events(r).exists()


@pytest.mark.django_db
def test_dormant_restore_and_conversion_keep_distinct_history_and_private_notes(lead):
    r = lead
    services.mark_customer_dormant(tenant=r.tenant, customer_id=r.customer.pk, user=r.user,
                                   reason_code='OTHER', reason_note='private reason text')
    services.restore_dormant_customer(tenant=r.tenant, customer_id=r.customer.pk, user=r.user,
                                     target_status='LEAD', reason_note='another private text')
    services.convert_to_active_customer(tenant=r.tenant, customer=r.customer, user=r.user)
    assert list(history(r).order_by('history_id').values_list('reason_code', flat=True)) == ['OTHER', 'REACTIVATED', 'LEAD_CONVERTED']
    assert events(r).count() == 3
    assert all('private' not in str(e.metadata) + e.description for e in events(r))


@pytest.mark.django_db
@pytest.mark.parametrize('operation', ['convert', 'dormant', 'restore'])
def test_status_change_rejects_other_company_without_any_write(lead, operation):
    r = lead
    if operation == 'restore':
        TenantCustomer.objects.filter(pk=r.customer.pk).update(customer_status='DORMANT')
    previous = TenantCustomer.objects.get(pk=r.customer.pk).customer_status
    with pytest.raises(Http404):
        if operation == 'convert':
            services.convert_to_active_customer(tenant=r.other, customer=r.customer, user=r.user)
        elif operation == 'dormant':
            services.mark_customer_dormant(tenant=r.other, customer_id=r.customer.pk, user=r.user, reason_code='OTHER')
        else:
            services.restore_dormant_customer(tenant=r.other, customer_id=r.customer.pk, user=r.user, target_status='ACTIVE')
    r.customer.refresh_from_db()
    assert r.customer.customer_status == previous
    assert not history(r).exists()
    assert not AuditLog.objects.filter(entity_type='CUSTOMER', entity_id=str(r.customer.pk)).exists()


@pytest.mark.parametrize('value', [None, True, False, 0, -1, '1', 1.5])
def test_alert_lock_rejects_missing_or_ambiguous_company(value):
    with pytest.raises(ValidationError):
        scope_locks.alert_scope_key(value)


def test_alert_lock_key_is_deterministic_company_scoped_signed_bigint():
    assert scope_locks.alert_scope_key(1) == scope_locks.alert_scope_key(N(tenant_id=1))
    assert scope_locks.alert_scope_key(1) != scope_locks.alert_scope_key(2)
    assert -(2**63) <= scope_locks.alert_scope_key(1) < 2**63


def test_postgresql_lock_is_parameterized_and_requires_atomic_transaction():
    cursor = Mock()
    fake = N(vendor='postgresql', in_atomic_block=True, cursor=Mock())
    fake.cursor.return_value.__enter__ = Mock(return_value=cursor)
    fake.cursor.return_value.__exit__ = Mock(return_value=False)
    with patch.object(scope_locks, 'connection', fake):
        scope_locks.lock_alert_scope(1)
        cursor.execute.assert_called_once_with('SELECT pg_advisory_xact_lock(%s)', [scope_locks.alert_scope_key(1)])
        fake.in_atomic_block = False
        with pytest.raises(RuntimeError):
            scope_locks.lock_alert_scope(1)
        fake.in_atomic_block = True
        fake.vendor = 'unsupported'
        with pytest.raises(ImproperlyConfigured):
            scope_locks.lock_alert_scope(1)


@pytest.mark.django_db
def test_alert_sync_acquires_company_lock_before_reading_sources():
    order = []
    with patch.object(alert_services, 'lock_alert_scope', side_effect=lambda t: order.append('lock')), \
         patch.object(alert_services, '_desired_alerts', side_effect=lambda t: (order.append('read'), (_ for _ in ()).throw(ValidationError('stop')))[1]):
        with pytest.raises(ValidationError):
            alert_services.synchronize_alerts(N(tenant_id=1))
    assert order == ['lock', 'read']


@pytest.mark.django_db
def test_alert_status_uses_same_lock_before_selecting_record():
    with patch.object(alert_services, 'lock_alert_scope') as lock, \
         patch.object(alert_services, 'get_object_or_404') as fetch:
        item = Mock()
        def find(*args, **kwargs):
            lock.assert_called_once_with(N(tenant_id=1))
            return item
        fetch.side_effect = find
        alert_services.update_alert_status(tenant=N(tenant_id=1), alert_id=99, status='SEEN')
        assert item.alert_status == 'SEEN'
        item.save.assert_called_once_with(update_fields=['alert_status'])


@pytest.fixture
def pg_records(transactional_db):
    if connection.vendor != 'postgresql':
        pytest.skip('Real PostgreSQL transaction/advisory lock test; SQLite is insufficient.')
    models = [Tenant, IndustrialUnit, TenantCustomer, CustomerStatusHistory, Alert]
    created = []
    with connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA IF NOT EXISTS core')
    for model in models:
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [model._meta.db_table])
            exists = cursor.fetchone()[0] is not None
        if not exists:
            with connection.schema_editor() as editor:
                editor.create_model(model)
            created.append(model)
    tag = uuid4().hex[:16]
    first = Tenant.objects.create(tenant_code='P01_' + tag + '_A', legal_name='Test A', display_name='Test A')
    second = Tenant.objects.create(tenant_code='P01_' + tag + '_B', legal_name='Test B', display_name='Test B')
    unit = IndustrialUnit.objects.create(official_name='P01 concurrency test', normalized_name='p01_' + tag)
    customer = TenantCustomer.objects.create(tenant_id=first.pk, industrial_unit=unit, customer_status='LEAD')
    yield N(tenant=first, other=second, customer=customer, user=actor())
    ids = [first.pk, second.pk]
    # Delete only this fixture's rows; never sweep pre-existing unmanaged tables.
    AuditLog.objects.filter(tenant_id__in=ids)._raw_delete('default')
    Alert.objects.filter(tenant_id__in=ids)._raw_delete('default')
    CustomerStatusHistory.objects.filter(tenant_id__in=ids)._raw_delete('default')
    TenantCustomer.objects.filter(pk=customer.pk)._raw_delete('default')
    IndustrialUnit.objects.filter(pk=unit.pk)._raw_delete('default')
    Tenant.objects.filter(pk__in=ids)._raw_delete('default')
    for model in reversed(created):
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def in_thread(fn):
    close_old_connections()
    try:
        return fn()
    finally:
        connection.close()


@contextmanager
def serialized_pair(first_call, second_call, attempted, release, entered):
    pool = ThreadPoolExecutor(max_workers=2)
    first = pool.submit(in_thread, first_call)
    second = None
    try:
        assert entered.wait(10), 'first writer did not reach protected section'
        second = pool.submit(in_thread, second_call)
        assert attempted.wait(10), 'second writer did not attempt lock'
        # First transaction remains open; second must not enter the protected read/write.
        yield first, second
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_conversion_records_exactly_one_transition(pg_records):
    r = pg_records
    entered, release, attempted = Event(), Event(), Event()
    original = services._record_customer_status_history
    gate = Lock()
    calls = [0]
    def held_history(**kwargs):
        with gate:
            calls[0] += 1
            first = calls[0] == 1
        if first:
            entered.set()
            assert release.wait(15)
        return original(**kwargs)
    def convert(*, second=False):
        if second:
            attempted.set()
        try:
            services.convert_to_active_customer(tenant=r.tenant, customer=r.customer, user=r.user)
            return 'changed'
        except Http404:
            return 'already_changed'
    with patch.object(services, '_record_customer_status_history', side_effect=held_history):
        with serialized_pair(convert, lambda: convert(second=True), attempted, release, entered) as (first, second):
            assert not second.done()
            release.set()
            assert first.result(timeout=15) == 'changed'
            assert second.result(timeout=15) == 'already_changed'
    assert history(r).count() == events(r).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('second_operation', ['sync', 'status'])
def test_postgresql_alert_writers_are_serialized_without_duplicate_rows(pg_records, second_operation):
    r = pg_records
    entered, release, attempted, read_twice = Event(), Event(), Event(), Event()
    gate = Lock()
    calls = [0]
    alert = Alert.objects.create(tenant_id=r.tenant.pk, tenant_customer_id=r.customer.pk,
                                 alert_type='CUSTOMER_NO_CONTACT', severity='NORMAL', alert_status='OPEN')
    real_lock = alert_services.lock_alert_scope
    lock_calls = [0]
    def observed_lock(tenant):
        with gate:
            lock_calls[0] += 1
            if lock_calls[0] == 2:
                attempted.set()
        return real_lock(tenant)
    def desired(tenant):
        with gate:
            calls[0] += 1
            first = calls[0] == 1
        if first:
            entered.set()
            assert release.wait(15)
        else:
            read_twice.set()
        values = dict(tenant_id=r.tenant.pk, tenant_customer_id=r.customer.pk,
                      follow_up_id=None, contract_id=None, alert_type='MISSED_CALL',
                      alert_at=None, severity='NORMAL', message='test alert')
        key = alert_services._alert_key('MISSED_CALL', customer_id=r.customer.pk)
        return {key: values}
    second_call = (lambda: alert_services.synchronize_alerts(r.tenant)) if second_operation == 'sync' else (
        lambda: alert_services.update_alert_status(tenant=r.tenant, alert_id=alert.pk, status='SEEN'))
    with patch.object(alert_services, 'lock_alert_scope', side_effect=observed_lock), \
         patch.object(alert_services, '_desired_alerts', side_effect=desired):
        with serialized_pair(lambda: alert_services.synchronize_alerts(r.tenant), second_call,
                             attempted, release, entered) as (first, second):
            assert not read_twice.wait(0.3) and not second.done()
            release.set()
            first.result(timeout=15)
            second.result(timeout=15)
    assert Alert.objects.filter(tenant_id=r.tenant.pk, alert_type='MISSED_CALL', tenant_customer_id=r.customer.pk).count() == 1
    if second_operation == 'status':
        alert.refresh_from_db()
        assert alert.alert_status == 'SEEN'


@pytest.mark.django_db(transaction=True)
def test_postgresql_alert_lock_does_not_block_other_company(pg_records):
    r = pg_records
    def other_company():
        with transaction.atomic():
            scope_locks.lock_alert_scope(r.other)
        return 'other-company-completed'
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            scope_locks.lock_alert_scope(r.tenant)
            other = pool.submit(in_thread, other_company)
            assert other.result(timeout=5) == 'other-company-completed'
