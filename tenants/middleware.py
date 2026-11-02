from .services import (
    ACTIVE_TENANT_SESSION_KEY,
    get_allowed_tenants,
    is_global_admin,
)


class TenantContextMiddleware:
    """شرکت مجاز فعال را به request.tenant متصل می‌کند."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.tenant_choices = []

        if request.user.is_authenticated:
            allowed_tenants = get_allowed_tenants(request.user)
            request.tenant_choices = allowed_tenants
            selected_id = request.session.get(ACTIVE_TENANT_SESSION_KEY)

            if selected_id:
                tenant = allowed_tenants.filter(
                    tenant_id=selected_id
                ).first()
                if tenant:
                    request.tenant = tenant
                else:
                    request.session.pop(ACTIVE_TENANT_SESSION_KEY, None)

            elif not is_global_admin(request.user):
                choices = list(allowed_tenants[:2])
                if len(choices) == 1:
                    request.tenant = choices[0]
                    request.session[ACTIVE_TENANT_SESSION_KEY] = choices[0].tenant_id

        return self.get_response(request)
