import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from .models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from .services import get_allowed_tenants
from .user_services import create_company_user


@pytest.fixture
def core_tenant_tables(db):
    statements = [
        "CREATE SCHEMA IF NOT EXISTS core",
        """CREATE TABLE IF NOT EXISTS core.tenants (
            tenant_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_code TEXT NOT NULL UNIQUE,
            legal_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.users (
            user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT,
            username TEXT,
            account_status TEXT NOT NULL DEFAULT 'PENDING_SETUP',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.roles (
            role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            role_code TEXT NOT NULL UNIQUE,
            role_name_fa TEXT NOT NULL,
            description TEXT,
            is_system_role BOOLEAN NOT NULL DEFAULT TRUE
        )""",
        """CREATE TABLE IF NOT EXISTS core.tenant_users (
            tenant_user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL REFERENCES core.tenants(tenant_id),
            user_id BIGINT NOT NULL REFERENCES core.users(user_id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS core.user_role_assignments (
            user_role_assignment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES core.users(user_id),
            role_id BIGINT NOT NULL REFERENCES core.roles(role_id),
            tenant_id BIGINT REFERENCES core.tenants(tenant_id),
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


@pytest.mark.django_db
def test_company_user_only_sees_assigned_tenant(core_tenant_tables):
    own = Tenant.objects.create(
        tenant_code="OWN", legal_name="Own", display_name="شرکت خودی", status="ACTIVE"
    )
    Tenant.objects.create(
        tenant_code="OTHER", legal_name="Other", display_name="شرکت دیگر", status="ACTIVE"
    )
    core_user = CoreUser.objects.create(
        full_name="کاربر تست", username="tenant_test", account_status="ACTIVE"
    )
    TenantMembership.objects.create(tenant=own, core_user=core_user, is_active=True)

    user = get_user_model().objects.create_user(username="tenant_test", password="Strong-Test-2026!")
    user.profile.core_user_id = core_user.user_id
    user.profile.save()

    allowed_ids = list(get_allowed_tenants(user).values_list("tenant_id", flat=True))
    assert allowed_ids == [own.tenant_id]


@pytest.mark.django_db
def test_superadmin_sees_all_active_tenants(core_tenant_tables):
    Tenant.objects.create(
        tenant_code="FIRST", legal_name="First", display_name="اول", status="ACTIVE"
    )
    Tenant.objects.create(
        tenant_code="SECOND", legal_name="Second", display_name="دوم", status="ACTIVE"
    )
    user = get_user_model().objects.create_superuser(
        username="global_admin", password="Strong-Test-2026!"
    )
    assert get_allowed_tenants(user).count() == 2


@pytest.mark.django_db
def test_create_company_user_builds_all_links(core_tenant_tables):
    tenant = Tenant.objects.create(
        tenant_code="COMPANY", legal_name="Company", display_name="شرکت", status="ACTIVE"
    )
    role = Role.objects.create(
        role_code="OPERATOR", role_name_fa="اپراتور عادی", is_system_role=True
    )
    auth_user, core_user = create_company_user(
        username="new_operator",
        full_name="اپراتور جدید",
        email="",
        password="Strong-Test-2026!",
        tenant=tenant,
        role=role,
    )

    assert auth_user.check_password("Strong-Test-2026!")
    assert auth_user.profile.core_user_id == core_user.user_id
    assert TenantMembership.objects.filter(tenant=tenant, core_user=core_user).exists()
    assert UserRoleAssignment.objects.filter(
        tenant=tenant, core_user=core_user, role=role
    ).exists()
    assert auth_user.groups.filter(name="OPERATOR").exists()


@pytest.fixture
def tenant_data_tables(core_tenant_tables):
    statements = [
        """CREATE TABLE IF NOT EXISTS core.tenant_customers (
            tenant_customer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            industrial_unit_id BIGINT NOT NULL,
            customer_status TEXT NOT NULL DEFAULT 'LEAD',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS core.contracts (
            contract_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL,
            tenant_customer_id BIGINT,
            counterparty_id BIGINT,
            parent_contract_id BIGINT,
            contract_direction TEXT NOT NULL,
            contract_number TEXT,
            external_reference TEXT,
            transaction_type TEXT NOT NULL DEFAULT 'UNKNOWN',
            contract_start_jalali TEXT,
            contract_start_gregorian DATE,
            contract_end_jalali TEXT,
            contract_end_gregorian DATE,
            delivery_start_jalali TEXT,
            delivery_start_gregorian DATE,
            delivery_end_jalali TEXT,
            delivery_end_gregorian DATE,
            payment_day INTEGER,
            billing_terms TEXT,
            contract_status TEXT NOT NULL DEFAULT 'IMPORTED',
            source_priority TEXT NOT NULL DEFAULT 'UNKNOWN',
            source_fingerprint TEXT NOT NULL DEFAULT '',
            data_quality_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return True


@pytest.mark.django_db
def test_customer_queryset_is_isolated_by_tenant(tenant_data_tables):
    from customers.models import TenantCustomer

    first = Tenant.objects.create(
        tenant_code="DATA_FIRST", legal_name="First", display_name="اول", status="ACTIVE"
    )
    second = Tenant.objects.create(
        tenant_code="DATA_SECOND", legal_name="Second", display_name="دوم", status="ACTIVE"
    )
    TenantCustomer.objects.create(tenant_id=first.tenant_id, industrial_unit_id=1)
    TenantCustomer.objects.create(tenant_id=second.tenant_id, industrial_unit_id=2)

    assert TenantCustomer.objects.for_tenant(first).count() == 1
    assert TenantCustomer.objects.for_tenant(second).count() == 1
    assert TenantCustomer.objects.for_tenant(None).count() == 0


@pytest.mark.django_db
def test_contract_queryset_is_isolated_by_tenant(tenant_data_tables):
    from contracts.models import Contract

    first = Tenant.objects.create(
        tenant_code="CONTRACT_FIRST", legal_name="First", display_name="اول", status="ACTIVE"
    )
    second = Tenant.objects.create(
        tenant_code="CONTRACT_SECOND", legal_name="Second", display_name="دوم", status="ACTIVE"
    )
    Contract.objects.create(tenant_id=first.tenant_id, contract_direction="SALE")
    Contract.objects.create(tenant_id=second.tenant_id, contract_direction="PURCHASE")

    assert list(
        Contract.objects.for_tenant(first).values_list("contract_direction", flat=True)
    ) == ["SALE"]
    assert list(
        Contract.objects.for_tenant(second).values_list("contract_direction", flat=True)
    ) == ["PURCHASE"]


@pytest.mark.django_db
def test_tampered_tenant_selection_is_rejected(core_tenant_tables, client):
    from django.urls import reverse
    from tenants.services import ACTIVE_TENANT_SESSION_KEY

    own = Tenant.objects.create(
        tenant_code="HTTP_OWN", legal_name="Own", display_name="شرکت مجاز", status="ACTIVE"
    )
    other = Tenant.objects.create(
        tenant_code="HTTP_OTHER", legal_name="Other", display_name="شرکت غیرمجاز", status="ACTIVE"
    )
    core_user = CoreUser.objects.create(
        full_name="کاربر محدود", username="limited_user", account_status="ACTIVE"
    )
    TenantMembership.objects.create(tenant=own, core_user=core_user, is_active=True)

    user = get_user_model().objects.create_user(
        username="limited_user", password="Strong-Test-2026!"
    )
    user.profile.core_user_id = core_user.user_id
    user.profile.save()
    client.force_login(user)

    response = client.post(reverse("tenants:select"), {"tenant": other.tenant_id})
    assert response.status_code == 200
    assert client.session.get(ACTIVE_TENANT_SESSION_KEY) == own.tenant_id
    assert "tenant" in response.context["form"].errors


@pytest.mark.django_db
def test_company_user_cannot_open_superadmin_tenant_list(core_tenant_tables, client):
    from django.urls import reverse

    own = Tenant.objects.create(
        tenant_code="ROLE_OWN", legal_name="Own", display_name="شرکت مجاز", status="ACTIVE"
    )
    core_user = CoreUser.objects.create(
        full_name="اپراتور محدود", username="role_user", account_status="ACTIVE"
    )
    TenantMembership.objects.create(tenant=own, core_user=core_user, is_active=True)

    user = get_user_model().objects.create_user(
        username="role_user", password="Strong-Test-2026!"
    )
    user.profile.core_user_id = core_user.user_id
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("tenants:list"))
    assert response.status_code == 403
