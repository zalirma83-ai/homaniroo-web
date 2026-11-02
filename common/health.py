from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


@require_GET
def health_check(request):
    database_ok = False
    migrations_ok = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            database_ok = cursor.fetchone()[0] == 1
        executor = MigrationExecutor(connection)
        migrations_ok = not executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:
        database_ok = False
        migrations_ok = False

    healthy = database_ok and migrations_ok
    response = JsonResponse(
        {
            "status": "ok" if healthy else "unavailable",
            "database": "ok" if database_ok else "unavailable",
            "migrations": "ok" if migrations_ok else "pending",
            "timestamp": timezone.now().isoformat(),
        },
        status=200 if healthy else 503,
    )
    response["Cache-Control"] = "no-store"
    return response
