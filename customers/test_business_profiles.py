from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.http import Http404
from django.test import RequestFactory
from django.template.loader import render_to_string
from django.urls import resolve, reverse

from customers.tests import active_customer_tables, customer_profile_tables, prospective_customer_tables
from customers import views as customer_views
from .business_services import (
    attach_business_classification, classify_customers, get_partner, partners_for_tenant,
)
from .business_views import partner_detail, partner_list
from .models import EntityBusinessProfile, IndustrialUnit, TenantCustomer


@pytest.fixture
def business_tables(prospective_customer_tables):
    with connection.cursor() as cursor:
        cursor.execute('''CREATE TABLE IF NOT EXISTS core.entity_business_profiles (
            profile_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id BIGINT NOT NULL, industrial_unit_id BIGINT NOT NULL,
            display_name TEXT NOT NULL, relationship_type TEXT NOT NULL,
            purchase_cycle TEXT NOT NULL, classification_status TEXT NOT NULL,
            review_reason TEXT, missing_in_initial_database BOOLEAN NOT NULL DEFAULT FALSE,
            notes TEXT, sources JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL, provided_national_id TEXT,
            UNIQUE (tenant_id, industrial_unit_id)
        )''')
    return True


def unit(name="واحد نمونه"):
    return IndustrialUnit.objects.create(official_name=name, normalized_name=name)


def profile(tenant_id=1, cycle="UNKNOWN", relationship="PARTNER", status="CONFIRMED", item=None, **extra):
    now = datetime(2026, 10, 18, tzinfo=dt_timezone.utc)
    return EntityBusinessProfile.objects.create(
        tenant_id=tenant_id, industrial_unit=item or unit(), display_name=extra.pop("display_name", "همکار نمونه"),
        relationship_type=relationship, purchase_cycle=cycle, classification_status=status,
        created_at=now, updated_at=now, **extra,
    )


def request(path="/customers/partners/", tenant_id=1, method="get"):
    req = getattr(RequestFactory(), method)(path)
    req.user = SimpleNamespace(is_authenticated=True, is_superuser=False, first_name="آزمایش", username="operator", groups=Group.objects.none())
    req.tenant = SimpleNamespace(tenant_id=tenant_id, display_name="شرکت آزمایش")
    req.resolver_match = resolve(req.path)
    return req


@pytest.mark.django_db
def test_partners_are_scoped_confirmed_and_not_ordinary_members(business_tables):
    expected = profile()
    profile(tenant_id=2)
    profile(relationship="CUSTOMER", cycle="ANNUAL")
    profile(status="REVIEW_REQUIRED")
    assert list(partners_for_tenant(1).values_list("pk", flat=True)) == [expected.pk]
    assert not partners_for_tenant(None).exists()
    assert TenantCustomer.objects.count() == 0


@pytest.mark.django_db
def test_same_name_units_remain_separate(business_tables):
    first, second = profile(), profile()
    assert first.industrial_unit_id != second.industrial_unit_id
    assert partners_for_tenant(1).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("cycle", ["ANNUAL", "MONTHLY"])
def test_cycle_filter_never_uses_foreign_or_unapproved_profile(business_tables, cycle):
    shared = unit()
    member = TenantCustomer.objects.create(tenant_id=1, industrial_unit=shared, customer_status="LEAD")
    profile(tenant_id=2, item=shared, relationship="CUSTOMER", cycle=cycle)
    qs = TenantCustomer.objects.for_tenant(1)
    assert classify_customers(qs, 1, cycle).count() == 0
    profile(tenant_id=1, item=shared, relationship="CUSTOMER", cycle=cycle, status="REVIEW_REQUIRED")
    assert classify_customers(qs, 1, cycle).count() == 0
    assert classify_customers(qs, 1, "UNCLASSIFIED").get().pk == member.pk


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["ACTIVE", "LEAD", "DORMANT"])
@pytest.mark.parametrize("cycle", ["ANNUAL", "MONTHLY"])
def test_classification_does_not_change_customer_lifecycle(business_tables, status, cycle):
    entry = profile(relationship="CUSTOMER", cycle=cycle)
    member = TenantCustomer.objects.create(tenant_id=1, industrial_unit_id=entry.industrial_unit_id, customer_status=status)
    result = classify_customers(TenantCustomer.objects.for_tenant(1), 1, cycle).get()
    assert result.pk == member.pk
    assert result.business_cycle == cycle
    assert result.business_relationship == "CUSTOMER"
    member.refresh_from_db()
    assert member.customer_status == status
    assert not classify_customers(TenantCustomer.objects.for_tenant(1), 1, "UNCLASSIFIED").exists()


@pytest.mark.django_db
def test_partner_membership_does_not_reclassify_as_customer(business_tables):
    entry = profile(cycle="ANNUAL")
    member = TenantCustomer.objects.create(tenant_id=1, industrial_unit_id=entry.industrial_unit_id, customer_status="ACTIVE")
    attach_business_classification(member, 1)
    assert member.business_relationship == "PARTNER"
    assert not classify_customers(TenantCustomer.objects.for_tenant(1), 1, "ANNUAL").exists()
    assert partners_for_tenant(1).get().customer_id == member.pk


@pytest.mark.django_db
def test_foreign_customer_membership_is_not_linked(business_tables):
    entry = profile()
    TenantCustomer.objects.create(tenant_id=2, industrial_unit_id=entry.industrial_unit_id)
    assert partners_for_tenant(1).get().customer_id is None
    with pytest.raises(Http404):
        get_partner(2, entry.pk)


@pytest.mark.django_db
def test_search_preserves_leading_zero_and_escapes_markup(business_tables):
    entry = profile(provided_national_id="001234", display_name='<script>alert("x")</script>')
    assert partners_for_tenant(1, "001234").get().pk == entry.pk
    response = partner_list(request(reverse("customers:partner_list") + "?q=001234"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "001234" in html
    assert "&lt;script&gt;" in html
    assert '<script>alert("x")</script>' not in html
    entry.refresh_from_db()
    assert entry.provided_national_id == "001234"


@pytest.mark.django_db
def test_partner_read_views_never_create_customer_or_update_profile(business_tables):
    entry = profile(notes="یادداشت ثابت")
    before = (TenantCustomer.objects.count(), list(EntityBusinessProfile.objects.values()))
    assert partner_list(request(reverse("customers:partner_list"))).status_code == 200
    path = reverse("customers:partner_detail", args=[entry.pk])
    assert partner_detail(request(path), entry.pk).status_code == 200
    assert partner_detail(request(path, method="post"), entry.pk).status_code == 405
    assert partner_list(request(reverse("customers:partner_list"), method="post")).status_code == 405
    assert (TenantCustomer.objects.count(), list(EntityBusinessProfile.objects.values())) == before


@pytest.mark.django_db
def test_partner_detail_rejects_foreign_customer_profile(business_tables):
    entry = profile(tenant_id=2)
    with pytest.raises(Http404):
        partner_detail(request(reverse("customers:partner_detail", args=[entry.pk])), entry.pk)


@pytest.mark.django_db
def test_partner_pagination_and_search_survive_page_changes(business_tables):
    for index in range(27):
        profile(display_name=f"همکار {index:02d}")
    response = partner_list(request(reverse("customers:partner_list") + "?q=همکار&page=2"))
    html = response.content.decode()
    assert response.status_code == 200
    assert html.count("مشاهده مشخصات") == 2
    assert "page=1" in html and "q=" in html


def test_business_model_is_unmanaged_and_menu_is_below_dormant():
    assert EntityBusinessProfile._meta.managed is False
    req = request(reverse("customers:partner_list"))
    html = render_to_string("partials/sidebar.html", {"request": req, "user": req.user})
    assert html.index("مشترکین راکد") < html.index("همکاران") < html.index("قراردادها")


@pytest.mark.parametrize("relationship,cycle,label", [
    ("CUSTOMER", "ANNUAL", "خرید سالانه"),
    ("CUSTOMER", "MONTHLY", "خرید ماهانه"),
    ("PARTNER", "ANNUAL", "همکار"),
    (None, None, "دوره خرید تعیین نشده"),
])
def test_badge_does_not_guess_classification(relationship, cycle, label):
    html = render_to_string("partials/business_badge.html", {"customer": SimpleNamespace(
        business_relationship=relationship, business_cycle=cycle,
    )})
    assert label in html


@pytest.mark.django_db
@pytest.mark.parametrize("view_name,status", [
    ("active_list", "ACTIVE"), ("prospective_list", "LEAD"), ("dormant_list", "DORMANT"),
])
def test_existing_lists_show_and_filter_purchase_cycle(business_tables, active_customer_tables, view_name, status):
    annual = profile(relationship="CUSTOMER", cycle="ANNUAL", display_name="سالانه")
    monthly = profile(relationship="CUSTOMER", cycle="MONTHLY", display_name="ماهانه")
    annual.industrial_unit.official_name = "واحد سالانه اختصاصی"
    annual.industrial_unit.save(update_fields=["official_name"])
    monthly.industrial_unit.official_name = "واحد ماهانه اختصاصی"
    monthly.industrial_unit.save(update_fields=["official_name"])
    for entry in [annual, monthly]:
        TenantCustomer.objects.create(tenant_id=1, industrial_unit_id=entry.industrial_unit_id, customer_status=status)
    path = reverse("customers:" + view_name)
    response = getattr(customer_views, view_name)(request(path + "?cycle=MONTHLY"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "واحد ماهانه اختصاصی" in html and "واحد سالانه اختصاصی" not in html
    assert "خرید ماهانه" in html
    assert TenantCustomer.objects.filter(customer_status=status).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("view_name,status", [
    ("active_detail", "ACTIVE"), ("prospective_detail", "LEAD"),
    ("dormant_detail", "DORMANT"), ("customer_profile", "ACTIVE"),
])
def test_existing_detail_displays_confirmed_classification(business_tables, customer_profile_tables, view_name, status):
    entry = profile(relationship="CUSTOMER", cycle="MONTHLY")
    member = TenantCustomer.objects.create(tenant_id=1, industrial_unit_id=entry.industrial_unit_id, customer_status=status)
    path = reverse("customers:" + view_name, args=[member.pk])
    response = getattr(customer_views, view_name)(request(path), member.pk)
    assert response.status_code == 200
    assert "خرید ماهانه" in response.content.decode()


@pytest.mark.django_db
def test_customer_classification_query_is_scoped_even_with_unfiltered_input(business_tables):
    entry = profile(tenant_id=2, relationship="CUSTOMER", cycle="MONTHLY")
    TenantCustomer.objects.create(tenant_id=2, industrial_unit_id=entry.industrial_unit_id)
    assert not classify_customers(TenantCustomer.objects.all(), 1).exists()
    assert not classify_customers(TenantCustomer.objects.all(), None).exists()


@pytest.mark.django_db
def test_anonymous_partner_request_redirects_to_login(business_tables):
    from django.contrib.auth.models import AnonymousUser
    req = request(reverse("customers:partner_list"))
    req.user = AnonymousUser()
    response = partner_list(req)
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_partner_request_requires_active_company(business_tables):
    from django.contrib.messages.storage.fallback import FallbackStorage
    req = request(reverse("customers:partner_list"))
    req.tenant = None
    req.session = {}
    req._messages = FallbackStorage(req)
    response = partner_list(req)
    assert response.status_code == 302
    assert response.url.startswith("/companies/select/")
