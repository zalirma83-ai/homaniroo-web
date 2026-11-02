import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from tenants.tests import core_tenant_tables

@pytest.mark.django_db
def test_anonymous_user_is_redirected_from_dashboard(client):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_login_and_logout(client):
    user = get_user_model().objects.create_user(
        username="test_operator",
        password="Strong-Test-Pass-2026!",
    )
    response = client.post(
        reverse("accounts:login"),
        {"username": user.username, "password": "Strong-Test-Pass-2026!"},
    )
    assert response.status_code == 302
    assert response.url == reverse("dashboard:home")

    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@pytest.mark.django_db
def test_operator_cannot_change_password(client):
    user = get_user_model().objects.create_user(
        username="password_tester",
        password="Old-Strong-Pass-2026!",
    )
    client.force_login(user)
    response = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": "Old-Strong-Pass-2026!",
            "new_password1": "New-Strong-Pass-2026!",
            "new_password2": "New-Strong-Pass-2026!",
        },
    )
    assert response.status_code == 403
    user.refresh_from_db()
    assert user.check_password("Old-Strong-Pass-2026!")


@pytest.mark.django_db
def test_regular_user_cannot_open_system_control(client):
    user = get_user_model().objects.create_user(
        username="regular_user",
        password="Strong-Test-Pass-2026!",
    )
    client.force_login(user)
    response = client.get(reverse("dashboard:system_control"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_superuser_can_open_system_control(client, core_tenant_tables):
    user = get_user_model().objects.create_superuser(
        username="system_admin",
        password="Strong-Test-Pass-2026!",
    )
    client.force_login(user)
    response = client.get(reverse("dashboard:system_control"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_user_can_view_active_sessions(client):
    user = get_user_model().objects.create_user(
        username="session_viewer",
        password="Strong-Test-Pass-2026!",
    )
    client.force_login(user)
    response = client.get(reverse("accounts:sessions"))
    assert response.status_code == 200
    assert "نشست فعلی" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_user_can_logout_other_sessions(client):
    from django.contrib.sessions.models import Session
    from django.test import Client

    user = get_user_model().objects.create_user(
        username="multi_session_user",
        password="Strong-Test-Pass-2026!",
    )
    client.force_login(user)
    current_key = client.session.session_key

    other_client = Client()
    other_client.force_login(user)
    other_key = other_client.session.session_key

    response = client.post(reverse("accounts:logout_other_sessions"))
    assert response.status_code == 302
    assert Session.objects.filter(session_key=current_key).exists()
    assert not Session.objects.filter(session_key=other_key).exists()


@pytest.mark.django_db
def test_user_can_logout_all_sessions(client):
    from django.contrib.sessions.models import Session

    user = get_user_model().objects.create_user(
        username="logout_all_user",
        password="Strong-Test-Pass-2026!",
    )
    client.force_login(user)
    old_key = client.session.session_key

    response = client.post(reverse("accounts:logout_all_sessions"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert not Session.objects.filter(session_key=old_key).exists()
