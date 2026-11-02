"""Focused regression tests for the shared shell; no production database access."""
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from .middleware import TenantContextMiddleware
from .services import ACTIVE_TENANT_SESSION_KEY


class _Tenants(list):
    def filter(self, **kwargs):
        return _Tenants(t for t in self if t.tenant_id == kwargs['tenant_id'])

    def first(self):
        return self[0] if self else None


def _company(pk, name):
    return SimpleNamespace(tenant_id=pk, display_name=name)


class ShellStepOneTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.own = _company(1, 'هما نیرو')
        self.other = _company(2, 'انرژی فرزان')

    def request(self, authenticated=True, admin=False):
        request = self.factory.get('/')
        request.user = SimpleNamespace(
            is_authenticated=authenticated, is_superuser=admin,
            is_staff=admin, username='shell_operator', get_full_name=lambda: 'اپراتور',
        )
        request.session = {}
        request.tenant = None
        request.tenant_choices = []
        return request

    def run_middleware(self, request, allowed, admin=False):
        with patch('tenants.middleware.get_allowed_tenants', return_value=_Tenants(allowed)), patch(
            'tenants.middleware.is_global_admin', return_value=admin
        ):
            TenantContextMiddleware(lambda req: HttpResponse('ok'))(request)
        return request

    def test_anonymous_has_no_company_choices(self):
        request = self.run_middleware(self.request(authenticated=False), [self.own])
        self.assertEqual(request.tenant_choices, [])
        self.assertIsNone(request.tenant)

    def test_single_allowed_company_still_auto_selects(self):
        request = self.run_middleware(self.request(), [self.own])
        self.assertEqual(request.session[ACTIVE_TENANT_SESSION_KEY], 1)
        self.assertEqual(request.tenant_choices, [self.own])

    def test_multiple_allowed_companies_do_not_auto_select(self):
        request = self.run_middleware(self.request(), [self.own, self.other])
        self.assertIsNone(request.tenant)
        self.assertNotIn(ACTIVE_TENANT_SESSION_KEY, request.session)

    def test_admin_still_chooses_explicitly(self):
        request = self.run_middleware(self.request(admin=True), [self.own], admin=True)
        self.assertIsNone(request.tenant)
        self.assertEqual(request.tenant_choices, [self.own])

    def test_forbidden_stored_company_is_cleared(self):
        request = self.request()
        request.session[ACTIVE_TENANT_SESSION_KEY] = 2
        request = self.run_middleware(request, [self.own])
        self.assertIsNone(request.tenant)
        self.assertNotIn(ACTIVE_TENANT_SESSION_KEY, request.session)
        self.assertEqual(request.tenant_choices, [self.own])

    def test_existing_allowed_selection_is_preserved(self):
        request = self.request()
        request.session[ACTIVE_TENANT_SESSION_KEY] = 2
        request = self.run_middleware(request, [self.own, self.other])
        self.assertIs(request.tenant, self.other)

    def test_header_lists_only_supplied_allowed_choices_and_keeps_session_menu(self):
        request = self.request()
        request.tenant_choices = [self.own]
        html = render_to_string('partials/topbar.html', {'request': request, 'user': request.user, 'csrf_token': 'x'})
        self.assertIn('name="tenant" value="1"', html)
        self.assertNotIn('name="tenant" value="2"', html)
        self.assertEqual(html.count(reverse('accounts:sessions')), 1)
        self.assertIn('method="post"', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertNotIn('href="' + reverse('tenants:select') + '"', html)
        self.assertIn('class="logout-button"', html)

    def test_header_highlights_active_company(self):
        request = self.request()
        request.tenant = self.other
        request.tenant_choices = [self.own, self.other]
        html = render_to_string('partials/topbar.html', {'request': request, 'user': request.user})
        self.assertIn('value="2" class="is-current" aria-current="true"', html)

    def test_sidebar_brand_uses_selected_company(self):
        request = self.request()
        request.tenant = self.other
        html = render_to_string('partials/sidebar.html', {'request': request, 'user': request.user})
        self.assertIn('<strong>انرژی فرزان</strong>', html)
        self.assertNotIn('<strong>هما نیرو</strong>', html)
        self.assertNotIn('href="' + reverse('tenants:select') + '"', html)
        self.assertNotIn('×', html)

    def test_sidebar_brand_before_selection_is_generic(self):
        request = self.request()
        html = render_to_string('partials/sidebar.html', {'request': request, 'user': request.user})
        self.assertIn('<strong>سامانه مدیریت مبادلات برق</strong>', html)

    def test_empty_dashboard_has_no_duplicate_company_selector(self):
        request = self.request()
        html = render_to_string('dashboard/home.html', {
            'request': request, 'user': request.user, 'dashboard_ready': False,
        })
        self.assertNotIn('href="' + reverse('tenants:select') + '"', html)
        self.assertEqual(html.count('action="' + reverse('tenants:select') + '"'), 1)
        self.assertIn('css/shell-step1.css', html)

    def test_fallback_selection_page_only_uses_header_chooser(self):
        request = self.request()
        html = render_to_string('tenants/select_tenant.html', {'request': request, 'user': request.user})
        self.assertNotIn('<select', html)
        self.assertEqual(html.count('action="' + reverse('tenants:select') + '"'), 1)
