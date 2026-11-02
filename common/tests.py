import pytest
from django.db import connection

@pytest.mark.django_db
def test_database_connection():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1



def test_jalali_datetime_filter():
    from datetime import datetime, timezone as dt_timezone

    from common.templatetags.jalali import jalali_datetime

    value = datetime(2024, 3, 20, 8, 30, tzinfo=dt_timezone.utc)
    result = jalali_datetime(value)
    assert "۱۴۰۳/۰۱/۰۱" in result
    assert "2024" not in result


@pytest.mark.django_db
def test_health_endpoint_reports_database_and_migrations(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["migrations"] == "ok"
    assert "timestamp" in payload
    assert "database_name" not in payload


def test_health_endpoint_is_read_only(client):
    response = client.post("/health/")
    assert response.status_code == 405


def test_standard_error_templates_exist():
    from django.template.loader import get_template

    for name in ("400.html", "403.html", "404.html", "500.html"):
        assert get_template(name) is not None


def test_requirements_do_not_contain_duplicate_lines():
    from django.conf import settings

    path = settings.BASE_DIR / "requirements.txt"
    lines = [line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == len(set(lines))


def test_env_example_contains_placeholders_not_runtime_secret():
    from django.conf import settings

    sample = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")
    assert "replace-with-a-long-random-secret" in sample
    assert settings.SECRET_KEY not in sample
    assert "DJANGO_DEBUG=False" in sample
