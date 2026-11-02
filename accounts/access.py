from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def superadmin_required(view_func):
    """Credential management is reserved for the active Django superuser."""
    @login_required
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped_view


def has_tenant_role(user, tenant, allowed_roles):
    """Operational permission: active auth account + membership + role in one tenant.

    Django groups and global (tenant=NULL) assignments are not company grants.
    Do not cache this decision in the session: role/membership revocation must apply
    to the next operation. This function never creates or updates access records.
    """
    tenant_id = getattr(tenant, "tenant_id", tenant)
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        return False
    if not (getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)):
        return False
    if not allowed_roles:
        return False
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "profile", None)
    core_user_id = getattr(profile, "core_user_id", None)
    if isinstance(core_user_id, bool) or not isinstance(core_user_id, int) or core_user_id <= 0:
        return False
    from tenants.models import UserRoleAssignment
    from tenants.services import can_access_tenant
    if not can_access_tenant(user, tenant_id):
        return False
    return UserRoleAssignment.objects.filter(
        tenant_id=tenant_id, core_user_id=core_user_id,
        role__role_code__in=allowed_roles,
    ).exists()


def role_required(*allowed_roles):
    """Company roles for operations; global routes require actual superuser."""

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            user = request.user
            if allowed_roles == ("SUPERADMIN",):
                # Legacy global administration is not a tenant operation.
                permitted = bool(user.is_active and user.is_superuser)
            else:
                permitted = has_tenant_role(user, getattr(request, "tenant", None), allowed_roles)
            if not permitted:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator
