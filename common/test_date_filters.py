import csv
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.utils import timezone

from common.date_filters import DateFilterRangeForm, parse_filter_date
from reports import services as report_services, views as report_views
from auditlog import services as audit_services, views as audit_views


class DateFiltersTests(SimpleTestCase):
    def request(self, path='/', params=None):
        request = RequestFactory().get(path, params or {})
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=True, is_staff=True, username='superadmin', get_full_name=lambda: 'سوپر ادمین')
        request.tenant = SimpleNamespace(tenant_id=1, display_name='هما نیرو')
        request.tenant_choices = [request.tenant]
        return request

    def test_empty_dates(self):
        self.assertIsNone(parse_filter_date(''))
        self.assertIsNone(parse_filter_date(None))
        form = DateFilterRangeForm({})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.iso_values, {'date_from': '', 'date_to': ''})

    def test_jalali_converts(self):
        self.assertEqual(parse_filter_date('1405/07/25'), date(2026, 10, 17))

    def test_persian_and_arabic_digits(self):
        for value in ['۱۴۰۵/۰۷/۲۵', '١٤٠٥/٠٧/٢٥', ' 1405/7/25 ']:
            with self.subTest(value=value):
                self.assertEqual(parse_filter_date(value), date(2026, 10, 17))

    def test_legacy_iso_links_supported(self):
        form = DateFilterRangeForm({'date_from': '2026-10-17'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.display_values['date_from'], '1405/07/25')
        self.assertEqual(form.iso_values['date_from'], '2026-10-17')

    def test_invalid_filters_not_silently_accepted(self):
        for value in ['not-a-date', '1405/13/01', '1405/07/31', '1400/12/30', '1405-07-25', '<script>alert(1)</script>', '1405/07/25 10:30']:
            with self.subTest(value=value):
                self.assertFalse(DateFilterRangeForm({'date_from': value}).is_valid())

    def test_leap_day(self):
        self.assertEqual(parse_filter_date('1399/12/30'), date(2021, 3, 20))

    def test_reversed_ranges_normalized(self):
        form = DateFilterRangeForm({'date_from': '1405/07/25', 'date_to': '1405/07/01'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.display_values, {'date_from': '1405/07/01', 'date_to': '1405/07/25'})

    def test_open_ended_range(self):
        form = DateFilterRangeForm({'date_to': '1405/07/25'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.iso_values, {'date_from': '', 'date_to': '2026-10-17'})

    def test_report_range_covers_full_last_day(self):
        with timezone.override('Asia/Tehran'):
            first, last, start, end = report_services.parse_report_dates('1405/07/25', '1405/07/25')
        self.assertEqual(first, date(2026, 10, 17))
        self.assertEqual(last, first)
        self.assertEqual(start, datetime(2026, 10, 17, tzinfo=ZoneInfo('Asia/Tehran')))
        self.assertEqual(end, datetime(2026, 10, 18, tzinfo=ZoneInfo('Asia/Tehran')))

    def test_report_parser_legacy_behavior(self):
        first, last, start, end = report_services.parse_report_dates('2026-10-10', '2026-10-01')
        self.assertEqual(first, date(2026, 10, 1))
        self.assertEqual(last, date(2026, 10, 10))
        self.assertIsNone(report_services.parse_report_dates('invalid', '')[0])

    def test_audit_parser_accepts_both_formats(self):
        self.assertEqual(audit_services._parse_date('1405/07/25'), date(2026, 10, 17))
        self.assertEqual(audit_services._parse_date('2026-10-17'), date(2026, 10, 17))
        self.assertIsNone(audit_services._parse_date('invalid'))

    def test_audit_filter_uses_gregorian_aware_bounds_and_tenant(self):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        queryset.order_by.return_value = queryset
        with patch.object(audit_services, 'AuditLog') as model, timezone.override('Asia/Tehran'):
            model.objects.all.return_value = queryset
            audit_services.filter_audit_logs(tenant_id='2', date_from='1405/07/25', date_to='1405/07/25')
        calls = [item.kwargs for item in queryset.filter.call_args_list]
        self.assertIn({'tenant_id': 2}, calls)
        self.assertIn({'created_at__gte': datetime(2026, 10, 17, tzinfo=ZoneInfo('Asia/Tehran'))}, calls)
        self.assertIn({'created_at__lt': datetime(2026, 10, 18, tzinfo=ZoneInfo('Asia/Tehran'))}, calls)

    def test_report_filter_keeps_inclusive_start_exclusive_end(self):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        _, _, start, end = report_services.parse_report_dates('1405/07/25', '1405/07/25')
        report_services.filter_period(queryset, 'interaction_at', start, end)
        self.assertEqual([item.kwargs for item in queryset.filter.call_args_list], [{'interaction_at__gte': start}, {'interaction_at__lt': end}])

    def test_report_view_displays_jalali_but_filters_gregorian(self):
        request = self.request('/reports/', {'from': '1405/07/25', 'to': '1405/07/25'})
        with patch.object(report_views, 'build_management_report', return_value={'filters': {}}) as build, patch.object(report_views, 'render', return_value=HttpResponse()) as render:
            report_views.report_center(request)
        self.assertEqual(build.call_args.kwargs, {'date_from': '2026-10-17', 'date_to': '2026-10-17'})
        self.assertEqual(render.call_args.args[2]['filters']['date_from'], '1405/07/25')
        self.assertEqual(build.call_args.args[0].tenant_id, 1)

    def test_report_csv_uses_same_range_and_keeps_numeric_data(self):
        request = self.request('/reports/export/interactions/', {'from': '1405/07/25', 'to': '1405/07/25'})
        with patch.object(report_views, 'export_rows', return_value=(['value'], [[-12.5]])) as export:
            response = report_views.export_csv(request, 'interactions')
        self.assertEqual(export.call_args.kwargs, {'date_from': '2026-10-17', 'date_to': '2026-10-17'})
        self.assertEqual(list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))[1], ['-12.5'])

    def test_audit_view_retains_other_filters_and_jalali_display(self):
        request = self.request('/system/audit/', {'date_from': '1405/07/25', 'tenant_id': '2', 'query': 'فولاد', 'page': '1'})
        def build(**filters):
            return {'filters': filters, 'queryset': []}
        with patch.object(audit_views, 'build_audit_context', side_effect=build) as build_mock, patch.object(audit_views, 'render', return_value=HttpResponse()) as render:
            audit_views.audit_center(request)
        self.assertEqual(build_mock.call_args.kwargs['date_from'], '2026-10-17')
        self.assertEqual(build_mock.call_args.kwargs['tenant_id'], '2')
        self.assertEqual(build_mock.call_args.kwargs['query'], 'فولاد')
        self.assertEqual(render.call_args.args[2]['filters']['date_from'], '1405/07/25')

    def test_audit_csv_uses_same_range_and_filters(self):
        request = self.request('/system/audit/export/', {'date_from': '1405/07/25', 'date_to': '1405/07/25', 'tenant_id': '2', 'query': 'فولاد'})
        with patch.object(audit_views, 'audit_export_rows', return_value=(['value'], [[1]])) as export:
            response = audit_views.audit_export(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(export.call_args.kwargs['date_from'], '2026-10-17')
        self.assertEqual(export.call_args.kwargs['date_to'], '2026-10-17')
        self.assertEqual(export.call_args.kwargs['tenant_id'], '2')
        self.assertEqual(export.call_args.kwargs['query'], 'فولاد')

    def test_invalid_date_blocks_all_four_queries(self):
        cases = [(report_views.report_center, report_views, 'build_management_report', {'from': 'wrong'}, ()), (report_views.export_csv, report_views, 'export_rows', {'from': 'wrong'}, ('interactions',)), (audit_views.audit_center, audit_views, 'build_audit_context', {'date_from': 'wrong'}, ()), (audit_views.audit_export, audit_views, 'audit_export_rows', {'date_from': 'wrong'}, ())]
        for view, module, name, params, args in cases:
            with self.subTest(view=view.__name__), patch.object(module, name) as query:
                response = view(self.request('/', params), *args)
                self.assertEqual(response.status_code, 400)
                self.assertIn('بازه تاریخ معتبر نیست', response.content.decode())
                query.assert_not_called()

    def test_all_four_inputs_and_cache_version(self):
        root = Path(__file__).resolve().parent.parent
        for relative, prefix in [('reports/report_center.html', 'report'), ('auditlog/audit_center.html', 'audit')]:
            text = (root/'templates'/relative).read_text(encoding='utf-8')
            for name in ['from', 'to']:
                self.assertIn(f'id="{prefix}_{name}" type="text" data-jalali-picker="date"', text)
            self.assertNotIn('تاریخ میلادی', text)
            self.assertNotIn('(شمسی)', text)
        self.assertIn("js/jalali-picker.js' %}?v=2", (root/'templates/base.html').read_text(encoding='utf-8'))

    def test_invalid_date_page_escapes_input_and_keeps_other_filters(self):
        request = self.request('/system/audit/', {'date_from': '<script>alert(1)</script>', 'tenant_id': '2', 'query': 'فولاد & انرژی', 'page': '4'})
        response = audit_views.audit_center(request)
        body = response.content.decode()
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('name="tenant_id" value="2"', body)
        self.assertIn('فولاد &amp; انرژی', body)
        self.assertNotIn('name="page"', body)
