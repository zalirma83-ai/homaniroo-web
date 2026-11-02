import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.utils import timezone

OUTPUT = Path(__file__).resolve().parent / "phase415_audit_security_profile.json"

TARGETS = [
    ("core", "tenants"),
    ("core", "users"),
    ("core", "roles"),
    ("core", "tenant_users"),
    ("core", "user_role_assignments"),
    ("core", "audit_logs"),
    ("core", "customer_status_history"),
    ("core", "interactions"),
    ("public", "auth_user"),
    ("public", "auth_group"),
    ("public", "auth_user_groups"),
    ("public", "accounts_userprofile"),
    ("public", "django_admin_log"),
    ("public", "django_session"),
    ("public", "axes_accessattempt"),
    ("public", "axes_accesslog"),
    ("public", "axes_accessfailurelog"),
]


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def fetch_all(cursor, sql, params=None):
    if params is None:
        cursor.execute(sql)
    else:
        cursor.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def safe_query(cursor, sql, params=None):
    try:
        return {"ok": True, "rows": fetch_all(cursor, sql, params)}
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


def exists(cursor, schema, table):
    cursor.execute("SELECT to_regclass(%s)", [f"{schema}.{table}"])
    return cursor.fetchone()[0] is not None


def profile(cursor, schema, table):
    quoted = f'"{schema}"."{table}"'
    cursor.execute(f"SELECT COUNT(*) FROM {quoted}")
    return {
        "exists": True,
        "row_count": cursor.fetchone()[0],
        "columns": fetch_all(
            cursor,
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            [schema, table],
        ),
        "constraints": fetch_all(
            cursor,
            """
            SELECT tc.constraint_name, tc.constraint_type
            FROM information_schema.table_constraints tc
            WHERE tc.table_schema = %s AND tc.table_name = %s
            ORDER BY tc.constraint_type, tc.constraint_name
            """,
            [schema, table],
        ),
    }


report = {
    "generated_at": timezone.now(),
    "phase": "4.15",
    "purpose": "Audit trail, access-control and security operations profile",
    "database_vendor": connection.vendor,
    "application": {
        "auditlog_installed": apps.is_installed("auditlog"),
        "axes_installed": apps.is_installed("axes"),
        "debug": settings.DEBUG,
        "session_cookie_age": settings.SESSION_COOKIE_AGE,
        "session_expire_at_browser_close": settings.SESSION_EXPIRE_AT_BROWSER_CLOSE,
        "session_save_every_request": settings.SESSION_SAVE_EVERY_REQUEST,
        "csrf_cookie_httponly": settings.CSRF_COOKIE_HTTPONLY,
        "csrf_cookie_secure": settings.CSRF_COOKIE_SECURE,
        "session_cookie_secure": settings.SESSION_COOKIE_SECURE,
        "axes_failure_limit": getattr(settings, "AXES_FAILURE_LIMIT", None),
        "axes_cooloff_time": getattr(settings, "AXES_COOLOFF_TIME", None),
        "authentication_backends": list(settings.AUTHENTICATION_BACKENDS),
    },
    "tables": {},
    "matching_tables": [],
    "analysis": {},
}

with connection.cursor() as cursor:
    report["matching_tables"] = fetch_all(
        cursor,
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ('core', 'public')
          AND (
              table_name ILIKE '%audit%'
              OR table_name ILIKE '%log%'
              OR table_name ILIKE '%access%'
              OR table_name ILIKE '%session%'
              OR table_name ILIKE '%user%'
              OR table_name ILIKE '%role%'
          )
        ORDER BY table_schema, table_name
        """,
    )

    for schema, table in TARGETS:
        key = f"{schema}.{table}"
        if exists(cursor, schema, table):
            report["tables"][key] = profile(cursor, schema, table)
        else:
            report["tables"][key] = {"exists": False}

    queries = {
        "tenant_distribution": """
            SELECT status, COUNT(*) AS tenant_count
            FROM core.tenants
            GROUP BY status ORDER BY status
        """,
        "core_user_status_distribution": """
            SELECT account_status, COUNT(*) AS user_count
            FROM core.users
            GROUP BY account_status ORDER BY account_status
        """,
        "role_catalog": """
            SELECT role_id, role_code, role_name_fa, is_system_role
            FROM core.roles ORDER BY role_id
        """,
        "tenant_membership_summary": """
            SELECT tu.tenant_id, t.display_name,
                   COUNT(*) AS membership_count,
                   COUNT(*) FILTER (WHERE tu.is_active) AS active_memberships,
                   COUNT(*) FILTER (WHERE NOT tu.is_active) AS inactive_memberships
            FROM core.tenant_users tu
            JOIN core.tenants t ON t.tenant_id = tu.tenant_id
            GROUP BY tu.tenant_id, t.display_name
            ORDER BY tu.tenant_id
        """,
        "role_assignment_summary": """
            SELECT ura.tenant_id, r.role_code, r.role_name_fa,
                   COUNT(*) AS assignment_count
            FROM core.user_role_assignments ura
            JOIN core.roles r ON r.role_id = ura.role_id
            GROUP BY ura.tenant_id, r.role_code, r.role_name_fa
            ORDER BY ura.tenant_id NULLS FIRST, r.role_code
        """,
        "auth_user_summary": """
            SELECT COUNT(*) AS auth_users,
                   COUNT(*) FILTER (WHERE is_active) AS active_users,
                   COUNT(*) FILTER (WHERE is_staff) AS staff_users,
                   COUNT(*) FILTER (WHERE is_superuser) AS superusers,
                   COUNT(*) FILTER (WHERE last_login IS NULL) AS never_logged_in,
                   MIN(last_login) AS first_last_login,
                   MAX(last_login) AS latest_login
            FROM public.auth_user
        """,
        "auth_group_distribution": """
            SELECT g.name AS group_name, COUNT(ug.user_id) AS user_count
            FROM public.auth_group g
            LEFT JOIN public.auth_user_groups ug ON ug.group_id = g.id
            GROUP BY g.id, g.name ORDER BY g.name
        """,
        "profile_linkage": """
            SELECT COUNT(*) AS profiles,
                   COUNT(*) FILTER (WHERE core_user_id IS NULL) AS missing_core_user,
                   COUNT(*) FILTER (WHERE must_change_password) AS must_change_password
            FROM public.accounts_userprofile
        """,
        "unlinked_auth_users": """
            SELECT au.id, au.username, au.is_active, au.is_superuser
            FROM public.auth_user au
            LEFT JOIN public.accounts_userprofile up ON up.user_id = au.id
            WHERE up.user_id IS NULL OR (up.core_user_id IS NULL AND NOT au.is_superuser)
            ORDER BY au.id
        """,
        "unlinked_core_users": """
            SELECT cu.user_id, cu.username, cu.full_name, cu.account_status
            FROM core.users cu
            LEFT JOIN public.accounts_userprofile up ON up.core_user_id = cu.user_id
            WHERE up.core_user_id IS NULL
            ORDER BY cu.user_id
        """,
        "session_summary": """
            SELECT COUNT(*) AS session_count,
                   COUNT(*) FILTER (WHERE expire_date > CURRENT_TIMESTAMP) AS active_sessions,
                   COUNT(*) FILTER (WHERE expire_date <= CURRENT_TIMESTAMP) AS expired_sessions,
                   MIN(expire_date) AS earliest_expiry,
                   MAX(expire_date) AS latest_expiry
            FROM public.django_session
        """,
        "admin_log_summary": """
            SELECT action_flag, COUNT(*) AS event_count,
                   MIN(action_time) AS first_event,
                   MAX(action_time) AS latest_event
            FROM public.django_admin_log
            GROUP BY action_flag ORDER BY action_flag
        """,
        "customer_status_audit_summary": """
            SELECT tenant_id, reason_code, COUNT(*) AS event_count,
                   COUNT(DISTINCT tenant_customer_id) AS affected_customers,
                   MIN(changed_at) AS first_event,
                   MAX(changed_at) AS latest_event
            FROM core.customer_status_history
            GROUP BY tenant_id, reason_code
            ORDER BY tenant_id, reason_code
        """,
        "customer_status_audit_actors": """
            SELECT tenant_id, changed_by_user_id, changed_by_name,
                   COUNT(*) AS event_count
            FROM core.customer_status_history
            GROUP BY tenant_id, changed_by_user_id, changed_by_name
            ORDER BY tenant_id, event_count DESC
        """,
        "interaction_source_summary": """
            SELECT tenant_id, source_system, interaction_status,
                   COUNT(*) AS event_count,
                   MIN(created_at) AS first_created,
                   MAX(created_at) AS latest_created
            FROM core.interactions
            GROUP BY tenant_id, source_system, interaction_status
            ORDER BY tenant_id, source_system, event_count DESC
        """,
        "axes_attempt_summary": """
            SELECT COUNT(*) AS attempt_rows,
                   COALESCE(SUM(failures_since_start), 0) AS recorded_failures,
                   MIN(attempt_time) AS first_attempt,
                   MAX(attempt_time) AS latest_attempt
            FROM public.axes_accessattempt
        """,
        "axes_access_log_summary": """
            SELECT logout_time IS NULL AS session_open, COUNT(*) AS access_count,
                   MIN(attempt_time) AS first_access,
                   MAX(attempt_time) AS latest_access
            FROM public.axes_accesslog
            GROUP BY logout_time IS NULL
            ORDER BY session_open DESC
        """,
        "axes_failure_log_summary": """
            SELECT COUNT(*) AS failure_count,
                   MIN(attempt_time) AS first_failure,
                   MAX(attempt_time) AS latest_failure
            FROM public.axes_accessfailurelog
        """,
    }

    for name, sql in queries.items():
        report["analysis"][name] = safe_query(cursor, sql)

OUTPUT.write_text(
    json.dumps(report, ensure_ascii=False, indent=2, default=json_default),
    encoding="utf-8",
)
print(f"Audit-security profile created: {OUTPUT}")
print(f"Tables inspected: {sum(1 for item in report['tables'].values() if item.get('exists'))}")
