"""Query-time result scopes; no historical audit row is rewritten.

The success flag on an HTTP event describes transport only. A recorded event
result is the producer's assertion, not an inference from the HTTP status.
"""
from django.db.models import Case, CharField, F, Q, TextField, Value, When
from django.db.models.functions import Concat

HTTP_AUDIT_SCOPE = "http_request"
RESULT_FILTER_LABELS = {
    "yes": "موفقِ ثبت‌شده (رویداد)",
    "no": "ناموفقِ ثبت‌شده (رویداد)",
    "http_ok": "HTTP بدون خطا؛ عملیات نامشخص",
    "http_error": "خطای HTTP؛ عملیات نامشخص",
    "legacy_http": "درخواست قدیمی؛ عملیات نامشخص",
}


def filter_audit_result(queryset, value):
    """Input must already carry the with_audit_outcomes annotations."""
    if value in {"yes", "no"}:
        return queryset.filter(audit_result_scope="event", success=value == "yes")
    if value in {"http_ok", "http_error"}:
        return queryset.filter(audit_result_scope="http", success=value == "http_ok")
    if value == "legacy_http":
        return queryset.filter(audit_result_scope="legacy_http")
    return queryset


def with_audit_outcomes(queryset, action_labels):
    new_http = Q(entity_type="http_request", action="SYSTEM",
                 metadata__audit_scope=HTTP_AUDIT_SCOPE)
    # Match the old middleware's distinctive shape, not every POST or empty entity.
    # This is a compatible historical HTTP signature, never proof of a DB write.
    legacy_http = (
        Q(entity_type="", entity_id="", request_path__startswith="/",
          request_method__in=("POST", "PUT", "PATCH", "DELETE"),
          metadata__has_keys=["status_code", "view_name"])
        & Q(description=Concat(F("request_method"), Value(" "), F("request_path"),
                               output_field=TextField()))
        & (Q(request_method="DELETE", action="DELETE")
           | Q(request_method__in=("POST", "PUT", "PATCH"), action="CREATE_OR_UPDATE"))
    )
    queryset = queryset.annotate(
        audit_result_scope=Case(
            When(new_http, then=Value("http")),
            When(legacy_http, then=Value("legacy_http")),
            default=Value("event"), output_field=CharField(),
        ),
    )
    return queryset.annotate(
        audit_action_label=Case(
            When(audit_result_scope="http", then=Value("درخواست HTTP")),
            When(audit_result_scope="legacy_http", then=Value("درخواست HTTP (قدیمی)")),
            *[When(action=code, then=Value(label)) for code, label in action_labels.items()],
            default=F("action"), output_field=CharField(),
        ),
        audit_result_label=Case(
            When(audit_result_scope="legacy_http", then=Value("قدیمی؛ نتیجهٔ عملیات نامشخص")),
            When(audit_result_scope="http", success=True, then=Value("HTTP بدون خطا؛ نتیجهٔ عملیات نامشخص")),
            When(audit_result_scope="http", success=False, then=Value("خطای HTTP؛ نتیجهٔ عملیات نامشخص")),
            When(success=True, then=Value("موفق (ثبت‌شده)")),
            default=Value("ناموفق (ثبت‌شده)"), output_field=CharField(),
        ),
        audit_result_class=Case(
            When(audit_result_scope__in=("http", "legacy_http"), then=Value("badge-muted")),
            When(success=True, then=Value("badge-success")),
            default=Value("badge-danger"), output_field=CharField(),
        ),
    )
