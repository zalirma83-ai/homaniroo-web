"""Presentation-only rate unit regression tests; no database access."""
from decimal import Decimal
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import TestCase

from django.template import Context, Engine


ROOT = Path(__file__).resolve().parent.parent
UNIT = 'ریال/کیلووات‌ساعت'
INCLUDE = '{% include "partials/rate_value.html" with rate_value=value only %}'


class RateUnitDisplayTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = Engine(
            dirs=[ROOT / 'templates'],
            builtins=['common.templatetags.jalali'],
            # Standalone Engine does not auto-discover Django tag libraries.
            # E04 adds a page-scoped stylesheet via {% load static %}.
            libraries={'static': 'django.templatetags.static'},
        )

    def test_static_library_is_available_to_page_templates(self):
        template = self.engine.from_string(
            "{% load static %}{% static 'css/e04-presentation.css' %}"
        )
        from django.templatetags.static import static
        self.assertEqual(
            template.render(Context()), static('css/e04-presentation.css')
        )

    def render_value(self, value):
        return self.engine.from_string(INCLUDE).render(Context({'value': value}, use_l10n=False)).strip()

    def test_positive_rate(self):
        html = self.render_value(Decimal('575'))
        self.assertIn('۵۷۵', html)
        self.assertEqual(html.count(UNIT), 1)

    def test_zero_rate_is_not_missing(self):
        html = self.render_value(Decimal('0'))
        self.assertIn('۰', html)
        self.assertIn(UNIT, html)
        self.assertNotIn('ثبت نشده', html)

    def test_none_has_no_unit(self):
        self.assertEqual(self.render_value(None), '<span class="muted">ثبت نشده</span>')

    def test_empty_string_has_no_unit(self):
        self.assertNotIn(UNIT, self.render_value(''))
        self.assertIn('ثبت نشده', self.render_value(''))

    def test_undefined_value_has_no_unit(self):
        html = self.engine.from_string('{% include "partials/rate_value.html" only %}').render(Context())
        self.assertIn('ثبت نشده', html)
        self.assertNotIn(UNIT, html)

    def test_newly_entered_rate_automatically_gets_unit(self):
        template = self.engine.from_string(INCLUDE)
        self.assertNotIn(UNIT, template.render(Context({'value': None})))
        self.assertIn(UNIT, template.render(Context({'value': Decimal('575')})))

    def test_negative_parentheses_are_preserved(self):
        html = self.render_value(Decimal('-575'))
        self.assertIn('(۵۷۵)', html)
        self.assertIn(UNIT, html)

    def test_numeric_formatting_is_unchanged(self):
        old = self.engine.from_string('{{ value|floatformat:"-6g"|fa_number }}')
        for value in (Decimal('575.123456'), Decimal('1000000'), Decimal('0'), Decimal('-12.5')):
            with self.subTest(value=value):
                number = old.render(Context({'value': value}, use_l10n=False))
                self.assertEqual(self.render_value(value), number + ' <small class="muted">' + UNIT + '</small>')

    def test_rate_values_are_not_mutated(self):
        value = Decimal('575.123456')
        self.render_value(value)
        self.assertEqual(value, Decimal('575.123456'))

    def test_existing_muted_style_is_reused(self):
        self.assertIn('<small class="muted">' + UNIT + '</small>', self.render_value(Decimal('575')))

    def test_active_customer_uses_shared_partial(self):
        text = (ROOT / 'templates/customers/active_detail.html').read_text(encoding='utf-8')
        self.assertEqual(text.count('include "partials/rate_value.html"'), 1)
        self.assertIn('rate_value=line.rate_value only', text)
        self.assertNotIn('line.item.rate_unit', text)

    def test_full_profile_uses_shared_partial(self):
        text = (ROOT / 'templates/customers/customer_profile.html').read_text(encoding='utf-8')
        self.assertEqual(text.count('include "partials/rate_value.html"'), 1)
        self.assertIn('rate_value=line.rate_value only', text)
        self.assertNotIn('line.item.rate_unit', text)

    def test_all_three_contract_rates_use_shared_partial(self):
        text = (ROOT / 'templates/contracts/contract_detail.html').read_text(encoding='utf-8')
        self.assertEqual(text.count('include "partials/rate_value.html"'), 3)
        for field in ('base_rate', 'corrected_rate', 'monthly_rate'):
            self.assertIn('rate_value=line.item.' + field + ' only', text)

    def test_actual_rate_cells_handle_blank_zero_and_future_values(self):
        pages = ('customers/active_detail.html', 'customers/customer_profile.html', 'contracts/contract_detail.html')
        for page in pages:
            text = (ROOT / 'templates' / page).read_text(encoding='utf-8')
            cells = re.findall(r'<td>({% include "partials/rate_value.html"[^\n]*?%})</td>', text)
            self.assertEqual(len(cells), 3 if page.startswith('contracts/') else 1)
            for cell in cells:
                for value in (None, Decimal('0'), Decimal('575')):
                    item = SimpleNamespace(base_rate=value, corrected_rate=value, monthly_rate=value)
                    line = SimpleNamespace(rate_value=value, item=item)
                    html = self.engine.from_string(cell).render(Context({'line': line}))
                    self.assertEqual(UNIT in html, value is not None)
                    self.assertEqual('ثبت نشده' in html, value is None)

    def test_amount_and_volume_cells_remain_present(self):
        for page in ('customers/active_detail.html', 'customers/customer_profile.html', 'contracts/contract_detail.html'):
            text = (ROOT / 'templates' / page).read_text(encoding='utf-8')
            self.assertIn('line.item.amount_value|floatformat:', text)
            self.assertIn('line.item.amount_unit', text)
            self.assertIn('line.item.contracted_volume|floatformat:"-6g"|fa_number', text)
            self.assertIn('line.item.volume_unit', text)

    def test_changed_templates_compile(self):
        for name in ('customers/active_detail.html', 'customers/customer_profile.html', 'contracts/contract_detail.html', 'partials/rate_value.html'):
            with self.subTest(name=name):
                self.engine.get_template(name)
