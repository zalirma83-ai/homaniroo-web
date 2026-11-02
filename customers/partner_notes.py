"""Operator notes are separate from imported business-classification history."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from auditlog.services import record_audit_event
from tenants.models import TenantMembership, UserRoleAssignment
from .models import EntityBusinessProfile, PartnerNote

EDIT_ROLES = ('COMPANY_ADMIN', 'SENIOR_OPERATOR', 'OPERATOR')
MAX_NOTE_LENGTH = 10000


class NoteConflict(Exception):
    """A submitted form is older than the currently stored note."""


def tenant_id_of(tenant):
    value = getattr(tenant, 'tenant_id', tenant)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def can_edit_partner_note(user, tenant):
    tenant_id = tenant_id_of(tenant)
    if not tenant_id or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    profile = getattr(user, 'profile', None)
    core_user_id = getattr(profile, 'core_user_id', None)
    if not core_user_id:
        return False
    # A global Django group is NOT a tenant-specific edit grant.
    if not TenantMembership.objects.filter(
        tenant_id=tenant_id, core_user_id=core_user_id,
        is_active=True, tenant__status='ACTIVE',
    ).exists():
        return False
    return UserRoleAssignment.objects.filter(
        tenant_id=tenant_id, core_user_id=core_user_id,
        role__role_code__in=EDIT_ROLES,
    ).exists()


def read_partner_note(tenant, profile_id):
    return PartnerNote.objects.for_tenant(tenant).filter(profile_id=profile_id).first()


def clean_note_body(value):
    if not isinstance(value, str):
        raise ValidationError('متن یادداشت معتبر نیست.')
    value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
    if '\x00' in value:
        raise ValidationError('متن یادداشت دارای نویسهٔ نامعتبر است.')
    if len(value) > MAX_NOTE_LENGTH:
        raise ValidationError('یادداشت حداکثر می‌تواند ۱۰۰۰۰ نویسه داشته باشد.')
    return value


def save_partner_note(*, tenant, profile_id, body, expected_version, user):
    if not can_edit_partner_note(user, tenant):
        raise PermissionDenied
    body = clean_note_body(body)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise ValidationError('نسخهٔ یادداشت معتبر نیست؛ صفحه را دوباره باز کنید.')
    tenant_id = tenant_id_of(tenant)
    with transaction.atomic():
        # Lock a stable existing profile even for the first note; never modify it.
        partner = get_object_or_404(
            EntityBusinessProfile.objects.for_tenant(tenant).select_for_update(),
            profile_id=profile_id, relationship_type='PARTNER', classification_status='CONFIRMED',
        )
        note = PartnerNote.objects.for_tenant(tenant).select_for_update().filter(profile_id=partner.pk).first()
        current_version = note.version if note else 0
        if expected_version != current_version:
            raise NoteConflict
        if (note and note.body == body) or (note is None and not body):
            return note, False
        name = (user.get_full_name() or user.get_username())[:300]
        if note is None:
            note = PartnerNote(tenant_id=tenant_id, profile_id=partner.pk, version=1)
        else:
            note.version += 1
        note.body = body
        note.updated_by_user_id = user.pk
        note.updated_by_name = name
        note.save()  # Database generates the identity; callers never supply an id.
        event = record_audit_event(
            action='CREATE_OR_UPDATE', user=user, tenant=tenant,
            entity_type='PARTNER_NOTE', entity_id=note.pk,
            description='ویرایش یادداشت اپراتور همکار',
            metadata={'profile_id': partner.pk, 'note_version': note.version, 'cleared': not bool(body)},
        )
        if event is None:
            raise ValidationError('ثبت سابقهٔ عملیات ممکن نشد؛ یادداشت ذخیره نشد. دوباره‌کاری نکنید و خطا را گزارش دهید.')
        return note, True
