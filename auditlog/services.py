from datetime import date, datetime, time, timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import DatabaseError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from common.date_filters import parse_filter_date
from tenants.models import Tenant
from .models import AuditLog
from .outcomes import filter_audit_result, with_audit_outcomes


ACTION_LABELS = {
    "LOGIN_SUCCESS": "ورود موفق",
    "LOGIN_FAILED": "ورود ناموفق",
    "LOGOUT": "خروج از سامانه",
    "CREATE_OR_UPDATE": "ایجاد یا ویرایش",
    "DELETE": "حذف",
    "STATUS_CHANGE": "تغییر وضعیت",
    "SYSTEM": "عملیات سامانه",
}
SEVERITY_LABELS = {"NORMAL": "عادی", "IMPORTANT": "مهم", "CRITICAL": "بحرانی"}


def request_ip(request):
    if request is None:
        return None
    value = request.META.get("REMOTE_ADDR")
    return value or None


def _identity(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None, None, ""
    core_user_id = None
    try:
        core_user_id = user.profile.core_user_id
    except Exception:
        pass
    return user.pk, core_user_id, user.get_username()


def record_audit_event(
    *, action, request=None, user=None, tenant=None, entity_type="", entity_id="",
    description="", metadata=None, success=True, severity="NORMAL",
):
    user = user or getattr(request, "user", None)
    tenant = tenant if tenant is not None else getattr(request, "tenant", None)
    auth_user_id, core_user_id, username = _identity(user)
    tenant_id = getattr(tenant, "tenant_id", tenant)
    path = (getattr(request, "path", "") or "") if request else ""
    method = (getattr(request, "method", "") or "") if request else ""
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    try:
        # The savepoint keeps an audit failure from breaking the business transaction.
        with transaction.atomic():
            return AuditLog.objects.create(
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                core_user_id=core_user_id,
                username=username,
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id or ""),
                description=description,
                metadata=safe_metadata,
                ip_address=request_ip(request),
                request_path=path,
                request_method=method,
                success=success,
                severity=severity if severity in SEVERITY_LABELS else "NORMAL",
            )
    except DatabaseError:
        return None


def _parse_date(value):
    try:
        return parse_filter_date(value)
    except (TypeError, ValueError):
        return None


def filter_audit_logs(*, query="", action="", severity="", success="", tenant_id="", date_from="", date_to=""):
    queryset = AuditLog.objects.all()
    if query:
        queryset = queryset.filter(
            Q(username__icontains=query) | Q(description__icontains=query)
            | Q(entity_type__icontains=query) | Q(entity_id__icontains=query)
            | Q(request_path__icontains=query)
        )
    if action in ACTION_LABELS:
        queryset = queryset.filter(action=action)
    if severity in SEVERITY_LABELS:
        queryset = queryset.filter(severity=severity)
    try:
        selected_tenant = int(tenant_id)
    except (TypeError, ValueError):
        selected_tenant = None
    if selected_tenant:
        queryset = queryset.filter(tenant_id=selected_tenant)
    current_tz = timezone.get_current_timezone()
    start_date, end_date = _parse_date(date_from), _parse_date(date_to)
    if start_date:
        start = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
        queryset = queryset.filter(created_at__gte=start)
    if end_date:
        end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), current_tz)
        queryset = queryset.filter(created_at__lt=end)
    queryset = with_audit_outcomes(queryset, ACTION_LABELS)
    queryset = filter_audit_result(queryset, success)
    return queryset.order_by("-created_at", "-audit_log_id")


def _safe_axes_stats():
    result = {"attempts": 0, "accesses": 0, "failures": 0, "locked": 0}
    for model_name, key in (("AccessAttempt", "attempts"), ("AccessLog", "accesses"), ("AccessFailureLog", "failures")):
        try:
            model = apps.get_model("axes", model_name)
            result[key] = model.objects.count()
            if model_name == "AccessFailureLog":
                result["locked"] = model.objects.filter(locked_out=True).count()
        except (LookupError, DatabaseError, AttributeError):
            pass
    return result


def security_summary():
    User = get_user_model()
    now = timezone.now()
    user_data = User.objects.aggregate(
        total=Count("id"), active=Count("id", filter=Q(is_active=True)),
        staff=Count("id", filter=Q(is_staff=True)),
        superusers=Count("id", filter=Q(is_superuser=True)),
        never_logged_in=Count("id", filter=Q(last_login__isnull=True)),
    )
    return {
        "users": user_data,
        "sessions": {
            "active": Session.objects.filter(expire_date__gt=now).count(),
            "expired": Session.objects.filter(expire_date__lte=now).count(),
        },
        "axes": _safe_axes_stats(),
    }


def build_audit_context(**filters):
    queryset = filter_audit_logs(**filters)
    today = timezone.localdate()
    current_tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(today, time.min), current_tz)
    totals = with_audit_outcomes(AuditLog.objects.all(), ACTION_LABELS).aggregate(
        total=Count("audit_log_id"),
        successful=Count("audit_log_id", filter=Q(audit_result_scope="event", success=True)),
        failed=Count("audit_log_id", filter=Q(audit_result_scope="event", success=False)),
        today=Count("audit_log_id", filter=Q(created_at__gte=today_start)),
        login=Count("audit_log_id", filter=Q(action__in=("LOGIN_SUCCESS", "LOGIN_FAILED"))),
        mutations=Count("audit_log_id", filter=Q(audit_result_scope="event", success=True, action__in=("CREATE_OR_UPDATE", "DELETE", "STATUS_CHANGE"))),
        http_requests=Count("audit_log_id", filter=Q(audit_result_scope="http")),
        legacy_http=Count("audit_log_id", filter=Q(audit_result_scope="legacy_http")),
    )
    tenant_ids = AuditLog.objects.exclude(tenant_id__isnull=True).values_list("tenant_id", flat=True).distinct()
    tenants = Tenant.objects.filter(tenant_id__in=tenant_ids).order_by("display_name")
    return {
        "queryset": queryset,
        "totals": totals,
        "security": security_summary(),
        "tenants": tenants,
        "action_labels": ACTION_LABELS,
        "severity_labels": SEVERITY_LABELS,
        "filters": filters,
    }


def audit_export_rows(**filters):
    header = ["زمان", "کاربر", "شرکت", "عملیات", "موجودیت", "شناسه", "شرح", "مسیر", "روش", "IP", "نتیجه", "اهمیت"]
    rows = []
    for item in filter_audit_logs(**filters):
        rows.append([
            item.created_at, item.username, item.tenant_id or "", item.audit_action_label,
            item.entity_type, item.entity_id, item.description, item.request_path,
            item.request_method, item.ip_address or "", item.audit_result_label,
            SEVERITY_LABELS.get(item.severity, item.severity),
        ])
    return header, rows
