"""One tenant-scoped manual ownership source; never infer or import ownership."""
from accounts.access import has_tenant_role
from types import SimpleNamespace
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404

from .models import EntityBusinessProfile, EntityOwnership, EntityOwnershipChange, IndustrialUnit, TenantCustomer, OWNERSHIP_CHOICES

LABELS = dict(OWNERSHIP_CHOICES)
EDIT_ROLES = ("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")


def normalize(value):
    return value if value in LABELS else ""


def can_edit(user, tenant=None):
    return has_tenant_role(user, tenant, EDIT_ROLES)


def annotate_ownership(queryset, tenant, unit_field="industrial_unit_id"):
    owned = EntityOwnership.objects.for_tenant(tenant).filter(industrial_unit_id=OuterRef(unit_field))
    return queryset.annotate(ownership_code=Coalesce(
        Subquery(owned.values("ownership_type")[:1]), Value("UNKNOWN"), output_field=CharField(),
    ))


def filter_customers(queryset, tenant, value=""):
    queryset = annotate_ownership(queryset.for_tenant(tenant), tenant)
    value = normalize(value)
    return queryset.filter(ownership_code=value) if value else queryset


def filter_related(queryset, tenant, value="", field="tenant_customer_id"):
    value = normalize(value)
    if not value:
        return queryset
    customers = filter_customers(TenantCustomer.objects.for_tenant(tenant), tenant, value)
    return queryset.filter(**{field + "__in": customers.values("tenant_customer_id")})


def attach_ownership(items, tenant, unit_field="industrial_unit_id"):
    items = list(items)
    ids = {getattr(item, unit_field) for item in items if getattr(item, unit_field, None)}
    mapping = {row.industrial_unit_id: row for row in EntityOwnership.objects.for_tenant(tenant).filter(industrial_unit_id__in=ids)}
    for item in items:
        code = getattr(item, unit_field, None)
        item.ownership = mapping.get(code) or SimpleNamespace(
            ownership_type="UNKNOWN", changed_at=None, changed_by_name="",
        )
        item.ownership_code = item.ownership.ownership_type
        item.ownership_label = LABELS.get(item.ownership_code, "نامشخص")
    return items


def accessible_unit(tenant, unit_id):
    # No name matching, no cross-tenant membership and no new customer creation.
    members = TenantCustomer.objects.for_tenant(tenant).filter(industrial_unit_id=unit_id)
    if not members.exists() and not EntityBusinessProfile.objects.for_tenant(tenant).filter(industrial_unit_id=unit_id).exists():
        from django.http import Http404
        raise Http404("واحد در شرکت فعال در دسترس نیست.")
    return get_object_or_404(IndustrialUnit, industrial_unit_id=unit_id)


@transaction.atomic
def set_ownership(*, tenant, unit_id, value, user):
    if not can_edit(user, tenant):
        raise PermissionDenied
    if value not in LABELS:
        raise ValidationError("نوع مالکیت معتبر نیست.")
    accessible_unit(tenant, unit_id)
    row, _ = EntityOwnership.objects.get_or_create(
        tenant_id=getattr(tenant, "tenant_id", tenant), industrial_unit_id=unit_id,
    )
    row = EntityOwnership.objects.select_for_update().get(pk=row.pk)
    previous = row.ownership_type
    if previous == value:
        return row
    actor = (user.get_full_name() or user.get_username())[:300]
    row.ownership_type = value
    row.changed_by_user_id = user.pk
    row.changed_by_name = actor
    row.save(update_fields=["ownership_type", "changed_by_user_id", "changed_by_name", "changed_at"])
    EntityOwnershipChange.objects.create(
        tenant_id=row.tenant_id, industrial_unit_id=unit_id, previous_type=previous, new_type=value,
        changed_by_user_id=user.pk, changed_by_name=actor,
    )
    return row


def preserve_on_identity_copy(tenant, old_unit_id, new_unit_id):
    """Existing prospective-edit copy-on-write must not lose an operator's choice."""
    previous = EntityOwnership.objects.for_tenant(tenant).filter(industrial_unit_id=old_unit_id).first()
    if previous:
        copied = EntityOwnership.objects.create(
            tenant_id=previous.tenant_id, industrial_unit_id=new_unit_id,
            ownership_type=previous.ownership_type, changed_by_user_id=previous.changed_by_user_id,
            changed_by_name=previous.changed_by_name,
        )

        EntityOwnership.objects.filter(pk=copied.pk).update(changed_at=previous.changed_at)
