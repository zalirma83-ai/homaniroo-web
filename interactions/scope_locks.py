"""Transaction-scoped serialization for alert writers; no schema changes."""
import hashlib

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection


def alert_scope_key(tenant):
    tenant_id = getattr(tenant, "tenant_id", tenant)
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValidationError("شناسه شرکت برای عملیات هشدار معتبر نیست.")
    value = f"homaniroo:alert-scope:v1:{tenant_id}".encode("ascii")
    digest = hashlib.blake2b(value, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def lock_alert_scope(tenant):
    key = alert_scope_key(tenant)
    if not connection.in_atomic_block:
        raise RuntimeError("Alert scope locking requires an active transaction.")
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    elif connection.vendor != "sqlite":
        raise ImproperlyConfigured("Alert scope locking supports PostgreSQL only.")
    # SQLite is used only by isolated local regression tests. It does not prove
    # PostgreSQL concurrency; the PostgreSQL-only threaded tests cover that path.
