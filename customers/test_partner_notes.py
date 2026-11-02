"""E06 storage, tenant permission and stale-form regression tests."""
from types import SimpleNamespace as N
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from auditlog.models import AuditLog
from tenants.models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from .models import EntityBusinessProfile, IndustrialUnit, PartnerNote, TenantCustomer
from .business_views import partner_detail, partner_note_update
from .partner_notes import NoteConflict, can_edit_partner_note, read_partner_note, save_partner_note

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def note_records(transactional_db):
    models = [Tenant, CoreUser, Role, TenantMembership, UserRoleAssignment,
              IndustrialUnit, TenantCustomer, EntityBusinessProfile]
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
    user = get_user_model().objects.create_user(username='e06_operator')
    auth_profile, _ = UserProfile.objects.get_or_create(user=user)
    core = CoreUser.objects.create(full_name='اپراتور آزمایشی', username='e06_core')
    auth_profile.core_user_id = core.pk
    auth_profile.save(update_fields=['core_user_id'])
    # Drop the cached reverse OneToOne value if a signal created it earlier.
    user = get_user_model().objects.get(pk=user.pk)
    first = Tenant.objects.create(tenant_code='E06_A', legal_name='A', display_name='شرکت الف')
    second = Tenant.objects.create(tenant_code='E06_B', legal_name='B', display_name='شرکت ب')
    membership = TenantMembership.objects.create(tenant=first, core_user=core)
    role = Role.objects.create(role_code='OPERATOR', role_name_fa='اپراتور')
    assignment = UserRoleAssignment.objects.create(tenant=first, core_user=core, role=role)
    unit = IndustrialUnit.objects.create(official_name='همکار مشترک', normalized_name='e06-unit')
    now = timezone.now()
    profiles = []
    for tenant in (first, second):
        profiles.append(EntityBusinessProfile.objects.create(
            tenant_id=tenant.pk, industrial_unit=unit, display_name='همکار نمونه',
            relationship_type='PARTNER', purchase_cycle='UNKNOWN', classification_status='CONFIRMED',
            notes='اطلاعات این مشتری یا شرکت در دیتابیس اولیه موجود نبود.',
            missing_in_initial_database=True, sources=[{'source': 'historical'}],
            created_at=now, updated_at=now,
        ))
    yield N(user=user, core=core, first=first, second=second, partner=profiles[0],
            other=profiles[1], unit=unit, role=role, assignment=assignment, membership=membership)
    # These are test-database fixtures only; drop only tables this fixture created.
    for model in reversed(created):
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def request(records, data=None, *, user=None, tenant=None, method='post', profile_id=None):
    profile_id = profile_id or records.partner.pk
    path = reverse('customers:partner_note_update', args=[profile_id])
    req = getattr(RequestFactory(), method)(path, data or {})
    req.user = user or records.user
    req.tenant = tenant or records.first
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def save(records, body='متن اپراتور', version=0, **kwargs):
    return save_partner_note(tenant=kwargs.pop('tenant', records.first),
        profile_id=kwargs.pop('profile_id', records.partner.pk), body=body,
        expected_version=version, user=kwargs.pop('user', records.user), **kwargs)


def test_initial_read_has_no_side_effect_and_no_imported_text(note_records):
    r = note_records
    assert read_partner_note(r.first, r.partner.pk) is None
    before = list(EntityBusinessProfile.objects.values())
    response = partner_detail(request(r, method='get'), r.partner.pk)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'ذخیره یادداشت' in html
    assert r.partner.notes not in html
    assert not PartnerNote.objects.exists()
    assert list(EntityBusinessProfile.objects.values()) == before


def test_save_edit_clear_retains_stale_form_protection(note_records):
    r = note_records
    original = list(EntityBusinessProfile.objects.values())
    note, changed = save(r)
    assert changed and note.pk is not None and note.version == 1
    note, changed = save(r, 'متن ویرایش‌شده', 1)
    assert changed and note.version == 2
    note, changed = save(r, '', 2)
    assert changed and note.version == 3 and note.body == ''
    assert PartnerNote.objects.count() == 1
    assert note.updated_by_user_id == r.user.pk
    assert list(EntityBusinessProfile.objects.values()) == original
    assert TenantCustomer.objects.count() == 0


def test_empty_first_save_and_identical_save_do_not_write(note_records):
    r = note_records
    # core.audit_logs may retain events across transactional tests on PostgreSQL.
    # Seed an older event so SQLite also exercises this isolation requirement.
    prior = AuditLog.objects.create(
        tenant_id=r.first.pk, auth_user_id=r.user.pk, entity_type='PARTNER_NOTE',
        entity_id='prior-test-event', action='CREATE_OR_UPDATE', description='prior test event',
    )
    events = AuditLog.objects.filter(entity_type='PARTNER_NOTE')
    before = set(events.values_list('pk', flat=True))
    assert save(r, '  ') == (None, False)
    assert not PartnerNote.objects.exists()
    assert set(events.values_list('pk', flat=True)) == before
    note, _ = save(r)
    after_save = set(events.values_list('pk', flat=True))
    assert len(after_save - before) == 1
    event = events.exclude(pk__in=before).get()
    assert event.tenant_id == r.first.pk and event.entity_id == str(note.pk)
    assert event.auth_user_id == r.user.pk
    stamp = note.updated_at
    note, changed = save(r, 'متن اپراتور', 1)
    assert not changed and note.version == 1 and note.updated_at == stamp
    assert set(events.values_list('pk', flat=True)) == after_save
    assert events.filter(pk=prior.pk, description='prior test event').exists()


def test_stale_first_form_cannot_replace_a_new_note(note_records):
    r = note_records
    save(r, 'نوشته اول')
    with pytest.raises(NoteConflict):
        save(r, 'نوشته کهنه', 0)
    assert PartnerNote.objects.get().body == 'نوشته اول'


def test_stale_edit_cannot_replace_or_clear_a_newer_note(note_records):
    r = note_records
    save(r)
    save(r, 'ویرایش تازه', 1)
    with pytest.raises(NoteConflict):
        save(r, '', 1)
    assert PartnerNote.objects.get().body == 'ویرایش تازه'


def test_two_tenants_keep_separate_notes_even_for_same_unit(note_records):
    r = note_records
    r.user.is_superuser = True
    save(r, 'شرکت الف')
    save(r, 'شرکت ب', tenant=r.second, profile_id=r.other.pk)
    assert read_partner_note(r.first, r.partner.pk).body == 'شرکت الف'
    assert read_partner_note(r.second, r.other.pk).body == 'شرکت ب'
    assert read_partner_note(r.first, r.other.pk) is None
    assert not PartnerNote.objects.for_tenant(None).exists()


def test_foreign_partner_id_is_rejected(note_records):
    r = note_records
    with pytest.raises(Http404):
        save(r, profile_id=r.other.pk)
    assert not PartnerNote.objects.exists()


@pytest.mark.parametrize('field,value', [('relationship_type','CUSTOMER'),('classification_status','REVIEW_REQUIRED')])
def test_nonpartner_or_unconfirmed_profile_cannot_receive_note(note_records, field, value):
    r = note_records
    EntityBusinessProfile.objects.filter(pk=r.partner.pk).update(**{field: value})
    with pytest.raises(Http404):
        save(r)
    assert not PartnerNote.objects.exists()


def test_global_group_is_not_a_company_role(note_records):
    r = note_records
    r.assignment.delete()
    r.user.groups.add(Group.objects.get_or_create(name='OPERATOR')[0])
    assert not can_edit_partner_note(r.user, r.first)
    with pytest.raises(PermissionDenied):
        save(r)


def test_foreign_company_role_does_not_allow_edit(note_records):
    r = note_records
    TenantMembership.objects.create(tenant=r.second, core_user=r.core)
    assert not can_edit_partner_note(r.user, r.second)
    with pytest.raises(PermissionDenied):
        save(r, tenant=r.second, profile_id=r.other.pk)


@pytest.mark.parametrize('mode', ['inactive_user','inactive_membership','inactive_tenant','missing_profile'])
def test_inactive_or_unlinked_actor_cannot_write(note_records, mode):
    r = note_records
    if mode == 'inactive_user':
        r.user.is_active = False
    elif mode == 'inactive_membership':
        TenantMembership.objects.filter(pk=r.membership.pk).update(is_active=False)
    elif mode == 'inactive_tenant':
        Tenant.objects.filter(pk=r.first.pk).update(status='INACTIVE')
    else:
        UserProfile.objects.filter(user=r.user).update(core_user_id=None)
        r.user = get_user_model().objects.get(pk=r.user.pk)
    with pytest.raises(PermissionDenied):
        save(r)


@pytest.mark.parametrize('role_code', ['COMPANY_ADMIN','SENIOR_OPERATOR','OPERATOR'])
def test_each_allowed_tenant_role_can_edit(note_records, role_code):
    r = note_records
    Role.objects.filter(pk=r.role.pk).update(role_code=role_code)
    assert can_edit_partner_note(r.user, r.first)
    save(r)


def test_audit_is_atomic_and_has_no_note_text(note_records):
    r = note_records
    prior = AuditLog.objects.create(
        tenant_id=r.first.pk, auth_user_id=r.user.pk, entity_type='PARTNER_NOTE',
        entity_id='prior-test-event', action='CREATE_OR_UPDATE', description='prior test event',
    )
    events = AuditLog.objects.filter(entity_type='PARTNER_NOTE')
    before = set(events.values_list('pk', flat=True))
    note, _ = save(r, 'private-note-body')
    fresh = events.exclude(pk__in=before)
    assert fresh.count() == 1
    event = fresh.get(tenant_id=r.first.pk, auth_user_id=r.user.pk, entity_id=str(note.pk))
    assert event.metadata['profile_id'] == r.partner.pk
    assert event.metadata['note_version'] == 1
    assert 'private-note-body' not in str(event.metadata) + event.description
    before_failure = set(events.values_list('pk', flat=True))
    with patch('customers.partner_notes.record_audit_event', return_value=None):
        with pytest.raises(ValidationError):
            save(r, 'must-not-save', 1)
    note.refresh_from_db()
    assert note.body == 'private-note-body' and note.version == 1
    assert set(events.values_list('pk', flat=True)) == before_failure
    assert events.filter(pk=prior.pk, description='prior test event').exists()


@pytest.mark.parametrize('invalid', [None, 'x'*10001, 'abc\x00def'], ids=['not-text','too-long','nul'])
def test_invalid_note_never_writes(note_records, invalid):
    with pytest.raises(ValidationError):
        save(note_records, invalid)
    assert not PartnerNote.objects.exists()


def test_database_unique_scope_and_positive_version_constraints(note_records):
    r = note_records
    save(r)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerNote.objects.create(tenant_id=r.first.pk, profile_id=r.partner.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerNote.objects.create(tenant_id=r.first.pk, profile_id=r.other.pk, version=0)


def test_update_is_post_only_and_csrf_protected(note_records):
    r = note_records
    assert partner_note_update(request(r, method='get'), r.partner.pk).status_code == 405
    req = request(r, {'body': 'text', 'version': 0})
    assert CsrfViewMiddleware(lambda request: None).process_view(req, partner_note_update, (), {}).status_code == 403
    assert not PartnerNote.objects.exists()


def test_invalid_version_returns_400_and_does_not_save(note_records):
    r = note_records
    response = partner_note_update(request(r, {'body':'text', 'version':'bad'}), r.partner.pk)
    assert response.status_code == 400
    assert not PartnerNote.objects.exists()


def test_successful_post_redirects_to_same_partner_without_open_redirect(note_records):
    r = note_records
    response = partner_note_update(request(r, {'body':'متن', 'version':0, 'next':'https://evil.invalid/'}), r.partner.pk)
    assert response.status_code == 302
    assert response.url == reverse('customers:partner_detail', args=[r.partner.pk])+'#operator-note'
    assert read_partner_note(r.first, r.partner.pk).body == 'متن'


def test_conflict_returns_409_preserves_submitted_text_and_newer_storage(note_records):
    r = note_records
    save(r, 'متن جدید')
    response = partner_note_update(request(r, {'body':'متن فرم قدیمی', 'version':0}), r.partner.pk)
    assert response.status_code == 409
    assert 'متن فرم قدیمی' in response.content.decode()
    assert 'چیزی بازنویسی نشد' in response.content.decode()
    assert read_partner_note(r.first, r.partner.pk).body == 'متن جدید'


def test_readonly_user_sees_escaped_note_without_edit_form(note_records):
    r = note_records
    save(r, '<script>alert(1)</script>')
    r.assignment.delete()
    response = partner_detail(request(r, method='get'), r.partner.pk)
    html = response.content.decode()
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html
    assert 'ذخیره یادداشت' not in html
    with pytest.raises(PermissionDenied):
        partner_note_update(request(r, {'body':'overwrite', 'version':1}), r.partner.pk)


def test_missing_tenant_never_authorizes_a_write(note_records):
    r = note_records
    with pytest.raises(PermissionDenied):
        save_partner_note(tenant=None, profile_id=r.partner.pk, body='text', expected_version=0, user=r.user)
    assert not PartnerNote.objects.exists()


def test_anonymous_request_is_redirected_to_login(note_records):
    from django.contrib.auth.models import AnonymousUser
    r = note_records
    response = partner_note_update(request(r, user=AnonymousUser()), r.partner.pk)
    assert response.status_code == 302 and '/account/login/' in response.url
    assert not PartnerNote.objects.exists()


def test_concurrent_initial_posts_on_postgresql_have_one_winner(note_records):
    if connection.vendor != 'postgresql':
        pytest.skip('Requires real PostgreSQL row-level locks; not claimed on SQLite.')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    r = note_records
    barrier = Barrier(2)
    def worker(body):
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=r.user.pk)
            barrier.wait(timeout=10)
            try:
                save_partner_note(tenant=r.first.pk, profile_id=r.partner.pk,
                                  body=body, expected_version=0, user=actor)
                return 'saved'
            except NoteConflict:
                return 'conflict'
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ['first writer', 'second writer']))
    assert sorted(results) == ['conflict', 'saved']
    assert PartnerNote.objects.count() == 1
    assert PartnerNote.objects.get().version == 1


@pytest.mark.parametrize('version', [True, -1, '1', None], ids=['boolean','negative','string','missing'])
def test_service_rejects_invalid_version_without_write(note_records, version):
    with pytest.raises(ValidationError):
        save(note_records, version=version)
    assert not PartnerNote.objects.exists()
