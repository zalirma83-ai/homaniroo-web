from django.db.models import Count

from .models import Tenant, TenantMembership

ACTIVE_TENANT_SESSION_KEY = "active_tenant_id"


def is_global_admin(user):
    """Only an active, authenticated Django superuser has global access."""
    return bool(getattr(user, "is_authenticated", False)
                and getattr(user, "is_active", False)
                and getattr(user, "is_superuser", False))


def get_allowed_tenants(user):
    """Fail closed for non-superusers with more than one active company.

    The count and selection are one SQL query, so adding a second active
    membership cannot expose both companies through a previously built queryset.
    No membership is selected or changed automatically.
    """
    if not (getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)):
        return Tenant.objects.none()
    if is_global_admin(user):
        return Tenant.objects.filter(status="ACTIVE").order_by("display_name")
    profile = getattr(user, "profile", None)
    core_user_id = getattr(profile, "core_user_id", None)
    if isinstance(core_user_id, bool) or not isinstance(core_user_id, int) or core_user_id <= 0:
        return Tenant.objects.none()
    sole_member = (TenantMembership.objects.filter(
        core_user_id=core_user_id, is_active=True, tenant__status="ACTIVE",
    ).values("core_user_id").annotate(tenant_count=Count("tenant_id", distinct=True))
        .filter(tenant_count=1).values("core_user_id"))
    return Tenant.objects.filter(
        status="ACTIVE", memberships__core_user_id__in=sole_member,
        memberships__is_active=True,
    ).distinct().order_by("display_name")


def can_access_tenant(user, tenant_id):
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        return False
    return get_allowed_tenants(user).filter(tenant_id=tenant_id).exists()
