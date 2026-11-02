"""Display-only negative-number regression checks; no database required."""
import csv
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.template import Context, Template
from django.template.defaultfilters import floatformat
from django.test import RequestFactory, SimpleTestCase
from django.utils import translation

from common.templatetags.jalali import fa_number, jalali_date, jalali_datetime


class NegativeNumberDisplayTests(SimpleTestCase):
    def test_negative_integer(self):
        self.assertEqual(fa_number(-659500), '(۶۵۹۵۰۰)')

    def test_negative_float(self):
        self.assertEqual(fa_number(-12.5), '(۱۲.۵)')

    def test_decimal_precision_and_trailing_zeros_are_preserved(self):
        self.assertEqual(fa_number(Decimal('-74733.870968')), '(۷۴۷۳۳.۸۷۰۹۶۸)')
        self.assertEqual(fa_number(Decimal('-12.3400')), '(۱۲.۳۴۰۰)')

    def test_thousands_separator_is_preserved(self):
        self.assertEqual(fa_number('-659,500'), '(۶۵۹,۵۰۰)')

    def test_persian_and_arabic_numeric_text(self):
        self.assertEqual(fa_number('-۶۵۹٬۵۰۰٫۲۵'), '(۶۵۹٬۵۰۰٫۲۵)')
        self.assertEqual(fa_number('−١٢٣٫٥'), '(١٢٣٫٥)')

    def test_unicode_minus_and_space_grouping(self):
        self.assertEqual(fa_number('−1\u00a0234.50'), '(۱\u00a0۲۳۴.۵۰)')
        self.assertEqual(fa_number('-1\u202f234'), '(۱\u202f۲۳۴)')

    def test_fraction_without_integer_part(self):
        self.assertEqual(fa_number('-.25'), '(.۲۵)')

    def test_scientific_notation_keeps_exponent(self):
        self.assertEqual(fa_number(Decimal('-1E-8')), '(۱E-۸)')

    def test_zero_and_positive_numbers_are_unchanged(self):
        for value, expected in [(0, '۰'), (659500, '۶۵۹۵۰۰'), ('659,500.25', '۶۵۹,۵۰۰.۲۵'), (Decimal('12.3400'), '۱۲.۳۴۰۰')]:
            with self.subTest(value=value):
                self.assertEqual(fa_number(value), expected)

    def test_none_empty_and_dash_are_unchanged(self):
        self.assertEqual(fa_number(None), '—')
        self.assertEqual(fa_number(''), '')
        self.assertEqual(fa_number('—'), '—')

    def test_signed_zero_keeps_its_original_sign_convention(self):
        self.assertEqual(fa_number(Decimal('-0.00')), '(۰.۰۰)')

    def test_already_parenthesized_number_is_not_wrapped_twice(self):
        self.assertEqual(fa_number('(659,500)'), '(۶۵۹,۵۰۰)')
        self.assertEqual(fa_number(fa_number(-125)), '(۱۲۵)')

    def test_dates_periods_and_contract_ids_are_not_treated_as_numbers(self):
        for value, expected in [('1400/02', '۱۴۰۰/۰۲'), ('2026-10-17', '۲۰۲۶-۱۰-۱۷'), ('CN-1405-01', 'CN-۱۴۰۵-۰۱'), ('-ABC123', '-ABC۱۲۳')]:
            with self.subTest(value=value):
                self.assertEqual(fa_number(value), expected)

    def test_phone_range_and_mixed_unit_text_are_not_rewritten(self):
        for value, expected in [('021-12345678', '۰۲۱-۱۲۳۴۵۶۷۸'), ('-12-34', '-۱۲-۳۴'), ('-12 MW', '-۱۲ MW'), ('متن -12', 'متن -۱۲')]:
            with self.subTest(value=value):
                self.assertEqual(fa_number(value), expected)

    def test_invalid_numbers_and_nonfinite_values_do_not_raise(self):
        for value, expected in [('-1,23', '-۱,۲۳'), ('--12', '--۱۲'), ('-12..5', '-۱۲..۵'), (Decimal('-Infinity'), '-Infinity'), (Decimal('NaN'), 'NaN')]:
            with self.subTest(value=value):
                self.assertEqual(fa_number(value), expected)

    def test_html_is_still_autoescaped(self):
        rendered = Template('{{ value|fa_number }}').render(Context({'value': '<script>-12</script>'}))
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)

    def test_raw_value_and_numeric_comparison_are_not_changed(self):
        value = Decimal('-659500.000001')
        before = value.as_tuple()
        fa_number(value)
        self.assertEqual(value.as_tuple(), before)
        self.assertLess(value, 0)
        self.assertEqual(value + Decimal('659500.000001'), 0)

    def test_floatformat_chain_preserves_its_existing_precision_and_grouping(self):
        value = Decimal('-74733.870968')
        for locale in ['en-us', 'fa-ir']:
            with translation.override(locale):
                formatted = str(floatformat(value, '-6g'))
                expected = '(' + formatted[1:].translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')) + ')'
                rendered = Template('{{ value|floatformat:"-6g"|fa_number }}').render(Context({'value': value}))
                self.assertEqual(rendered, expected)

    def test_existing_conditional_still_sees_negative_numeric_value(self):
        value = Decimal('-10')
        rendered = Template('{% if value < 0 %}کسری{% else %}مازاد{% endif %}: {{ value|fa_number }}').render(Context({'value': value}))
        self.assertEqual(rendered, 'کسری: (۱۰)')

    def test_jalali_date_and_datetime_filters_are_unchanged(self):
        self.assertEqual(jalali_date(date(2024, 3, 20)), '۱۴۰۳/۰۱/۰۱')
        self.assertIn('۱۴۰۳/۰۱/۰۱', jalali_datetime(datetime(2024, 3, 20, 8, 30, tzinfo=dt_timezone.utc)))

    def test_csv_export_keeps_machine_readable_negative_number(self):
        from reports.views import export_csv
        request = RequestFactory().get('/reports/export/energy/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True)
        request.tenant = SimpleNamespace(tenant_id=1)
        with patch('reports.views.export_rows', return_value=(['balance'], [[Decimal('-659500.25')]])):
            response = export_csv(request, 'energy')
        rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows, [['balance'], ['-659500.25']])
