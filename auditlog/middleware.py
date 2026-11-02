from .services import record_audit_event
from .outcomes import HTTP_AUDIT_SCOPE


class AuditTrailMiddleware:
    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    EXCLUDED_PATHS = {"/account/login/", "/account/logout/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.method in self.MUTATING_METHODS
            and request.path not in self.EXCLUDED_PATHS
            and getattr(request, "user", None)
            and request.user.is_authenticated
        ):
            http_ok = response.status_code < 400
            match = getattr(request, "resolver_match", None)
            view_name = getattr(match, "view_name", "") if match else ""
            record_audit_event(
                action="SYSTEM",
                entity_type="http_request",
                request=request,
                description=f"پاسخ HTTP {response.status_code} برای {request.method} {request.path}؛ نتیجهٔ عملیات نامشخص است.",
                metadata={"audit_scope": HTTP_AUDIT_SCOPE, "business_outcome": "UNKNOWN",
                          "status_code": response.status_code, "view_name": view_name},
                success=http_ok,
                severity="NORMAL" if http_ok else "IMPORTANT",
            )
        return response
