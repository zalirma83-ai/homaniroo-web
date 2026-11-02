"""E04 UI regression tests. No database or imported data is required."""
import inspect
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from django.core.paginator import Paginator
from django.template import Context
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase

from common.templatetags.pagination import numbered_pagination


class Tables(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tables = []
        self.current = None
        self.row = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            self.current = {'class': attrs.get('class', ''), 'rows': []}
            self.tables.append(self.current)
        elif tag == 'tr' and self.current is not None:
            self.row = []
            self.current['rows'].append(self.row)
        elif tag in ('td', 'th') and self.row is not None:
            self.row.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'tr':
            self.row = None
        elif tag == 'table':
            self.current = None


class E04PresentationTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.user = N(is_authenticated=True, is_superuser=True, is_staff=True,
                      username='qa', get_full_name=lambda: 'کاربر آزمایشی')
        self.tenant = N(tenant_id=1, display_name='شرکت آزمایشی')
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.request.tenant = self.tenant
        self.request.tenant_choices = [self.tenant]
        self.info = N(incomplete=True, review=True, summary='E04_PRIVATE_QUALITY_SENTINEL',
                      label='نیازمند تکمیل و نیازمند بررسی', issues=[])
        self.unit = N(industrial_unit_id=3, official_name='مشتری آزمایشی', national_id=None)
        self.customer = N(tenant_customer_id=223, tenant_id=1, industrial_unit=self.unit,
                          customer_status='ACTIVE', information=self.info,
                          ownership_code='UNKNOWN', ownership=None,
                          contact_person_display='', mobile_display='', phone_display='',
                          has_known_volume=False, contract_count=1)
        self.item = N(contract_id=1179, tenant_id=1, contract_direction='SALE',
                      contract_number='C-1179', total_volume=None, total_amount=None,
                      transaction_type='BILATERAL', line_count=1)
        self.contract_row = N(item=self.item, information=self.info,
                              display_number='C-1179', party_name='مشتری آزمایشی',
                              customer=self.customer, direction_label='فروش', transaction_label='دوجانبه',
                              start_display=None, end_display=None,
                              lifecycle_class='badge-warning', lifecycle_label='تاریخ نامشخص',
                              energy_labels=['برق حرارتی'], lines=[])

    def render_page(self, name, **context):
        return render_to_string(name, {
            'request': self.request, 'user': self.user, 'csrf_token': 'qa',
            'filters': {}, 'stats': {}, 'page': Paginator([], 25).get_page(1), **context,
        })

    def assert_no_quality(self, html):
        for text in ('وضعیت اطلاعات', 'موارد نیازمند رسیدگی', 'نیازمند تکمیل',
                     'نیازمند بررسی', 'E04_PRIVATE_QUALITY_SENTINEL', 'name="quality"'):
            self.assertNotIn(text, html)

    def test_all_project_templates_compile(self):
        for path in (self.root / 'templates').rglob('*.html'):
            with self.subTest(path=str(path)):
                get_template(str(path.relative_to(self.root / 'templates')))

    def test_all_page_sources_have_no_quality_partial_calls(self):
        for path in (self.root / 'templates').rglob('*.html'):
            if path.parent.name == 'partials':
                continue
            with self.subTest(path=str(path)):
                source = path.read_text(encoding='utf-8-sig')
                self.assertNotIn('partials/information_badges.html', source)
                self.assertNotIn('partials/information_details.html', source)
                self.assertNotIn('name="quality"', source)
                self.assertNotIn('information.summary', source)

    def test_empty_list_tables_have_matching_headers_and_colspan(self):
        samples = [('customers/active_list.html', 'active-customer-table', 6),
                   ('customers/prospective_list.html', 'prospective-table', 5),
                   ('contracts/contract_list.html', 'contract-list-table', 6)]
        for template, css_class, count in samples:
            with self.subTest(template=template):
                html = self.render_page(template)
                self.assert_no_quality(html)
                table = next(t for t in Tables(html).tables if css_class in t['class'].split())
                self.assertEqual(len(table['rows'][0]), count)
                self.assertEqual(table['rows'][1][0][1].get('colspan'), str(count))

    def test_customer_rows_have_no_empty_quality_cell(self):
        for template, css_class, count in [('customers/active_list.html', 'active-customer-table', 6),
                                          ('customers/prospective_list.html', 'prospective-table', 5)]:
            with self.subTest(template=template):
                html = self.render_page(template, page=Paginator([self.customer], 25).get_page(1))
                self.assert_no_quality(html)
                table = next(t for t in Tables(html).tables if css_class in t['class'].split())
                self.assertEqual(len(table['rows'][1]), count)
                self.assertIn('مشتری آزمایشی', html)
                self.assertIn('مشاهده پرونده', html)
                self.assertIn('ثبت نشده' if 'active' in template else 'بدون سابقه', html)
                self.assertIn('نوع مالکیت', html)
                self.assertIn('دوره خرید تأییدشده', html)

    def test_contract_row_keeps_missing_values_and_line_count(self):
        html = self.render_page('contracts/contract_list.html', rows=[self.contract_row])
        self.assert_no_quality(html)
        table = next(t for t in Tables(html).tables if 'contract-list-table' in t['class'].split())
        self.assertEqual(len(table['rows'][1]), 6)
        for text in ('C-1179', 'حجم ثبت نشده', 'مبلغ ثبت نشده', '۱ ردیف انرژی', 'تاریخ نامشخص'):
            self.assertIn(text, html)
        self.assertIn('badge-muted">تاریخ نامشخص', html)
        self.assertIn('name="date"', html)
        self.assertIn('name="ownership"', html)

    def test_profile_and_detail_pages_hide_quality_without_hiding_identity(self):
        samples = [
            ('customers/customer_profile.html', {'customer': self.customer}),
            ('customers/active_detail.html', {'customer': self.customer, 'contacts': [],
                                            'contract_data': {'rows': [self.contract_row], 'contracts_missing_dates': 1}}),
            ('customers/prospective_detail.html', {'customer': self.customer}),
            ('contracts/contract_detail.html', {'contract': self.item, 'row': self.contract_row}),
        ]
        for template, context in samples:
            with self.subTest(template=template):
                html = self.render_page(template, **context)
                self.assert_no_quality(html)
                self.assertIn('مشتری آزمایشی', html)
                self.assertNotIn('active-data-warning', html)
                self.assertIn('نوع مالکیت', html)

    def test_dashboard_keeps_unknown_counts_and_operational_warnings(self):
        html = self.render_page('dashboard/home.html', dashboard_ready=True,
            dashboard_date=date(2026, 10, 27),
            contracts={'active_display': None, 'expiring_display': None, 'dates_available': False},
            follow_ups={'overdue': 2, 'today': 1}, alerts={'open': 4},
            quality={'customer_review': 99, 'contract_review': 99})
        self.assert_no_quality(html)
        self.assertNotIn('به دلیل نبود تاریخ معتبر', html)
        self.assertIn('<strong>—</strong>', html)
        self.assertIn('تا ۳۰ روز آینده', html)
        self.assertIn('dashboard-metric-warning', html)
        self.assertIn('مورد عقب‌افتاده', html)
        self.assertIn('هشدارهای عملیاتی باز', html)

    def test_reports_keep_exports_dates_and_financial_values(self):
        html = self.render_page('reports/report_center.html', energy={
            'totals': {'balance': -12, 'purchase': 0, 'sales': 12, 'incomplete': 9},
            'monthly': [{'period_jalali': '1405/08', 'purchase': 0, 'sales': 12, 'balance': -12, 'incomplete': 9}],
        })
        self.assert_no_quality(html)
        self.assertNotIn('ردیف ناقص', html)
        self.assertIn('خروجی CSV', html)
        self.assertIn('data-jalali-picker="date"', html)
        self.assertIn('(۱۲)', html)
        self.assertIn('MW', html)
        tables = Tables(html).tables
        self.assertEqual(len(tables[0]['rows'][0]), 4)
        self.assertEqual(len(tables[0]['rows'][1]), 4)

    def test_e08_energy_table_is_not_removed_by_e04(self):
        html = self.render_page('energy/trading_dashboard.html', summary={
            'purchase': 1, 'sales': 2, 'balance': -1, 'incomplete': 1})
        self.assertIn('کامل‌بودن', html)
        self.assertIn('ردیف ناقص تراز', html)

    def test_real_operational_alert_and_post_actions_remain(self):
        row = {'item': N(alert_id=1, alert_at=None, alert_status='OPEN',
                        message='پیگیری عقب افتاده است؛ نیازمند بررسی اپراتور.'),
               'type_label': 'پیگیری مشتری', 'status_label': 'باز', 'customer': None,
               'severity_class': 'warning', 'severity_label': 'مهم'}
        html = self.render_page('interactions/alert_center.html', page=Paginator([row],25).get_page(1))
        self.assertIn(row['item'].message, html)
        self.assertIn('badge-warning', html)
        self.assertIn('method="post"', html)
        self.assertIn('رسیدگی شد', html)

    def test_pagination_opt_in_drops_only_retired_parameter(self):
        request = RequestFactory().get('/customers/active/?q=abc&quality=CLEAN&quality=INCOMPLETE&ownership=PUBLIC&cycle=MONTHLY&tag=a&tag=b&page=2')
        result = numbered_pagination(Context({'request': request}), Paginator(range(100), 25).get_page(2), ignored_params='quality')
        params = parse_qs(result['next_url'][1:])
        self.assertNotIn('quality', params)
        self.assertEqual(params['q'], ['abc'])
        self.assertEqual(params['ownership'], ['PUBLIC'])
        self.assertEqual(params['cycle'], ['MONTHLY'])
        self.assertEqual(params['tag'], ['a', 'b'])
        self.assertNotIn('quality', [k for k, _ in result['hidden_filters']])
        self.assertEqual(request.GET.getlist('quality'), ['CLEAN', 'INCOMPLETE'])
        default = numbered_pagination(Context({'request':request}), Paginator(range(100),25).get_page(2))
        self.assertIn('quality', parse_qs(default['next_url'][1:]))

    def test_customer_views_ignore_old_quality_url_but_keep_other_filters(self):
        from customers import views
        for name, query_fn, filter_fn in [
            ('active_list', 'active_customers_for_tenant', 'filter_active_customers'),
            ('prospective_list', 'prospective_customers_for_tenant', 'filter_prospective_customers')]:
            with self.subTest(view=name):
                request = RequestFactory().get('/?quality=CLEAN&q=steel&order=name&ownership=PUBLIC&cycle=MONTHLY')
                request.tenant = self.tenant
                base = Mock()
                base.count.return_value = 0
                base.filter.return_value = base
                page = N(object_list=[])
                with patch.object(views, query_fn, return_value=base), \
                     patch.object(views, filter_fn, return_value=base) as filtering, \
                     patch.object(views, 'classify_customers', return_value=base), \
                     patch.object(views, 'filter_information', return_value=base), \
                     patch.object(views, 'Contract') as contracts, \
                     patch.object(views, 'Paginator') as paginator, \
                     patch.object(views, 'attach_customer_information', return_value=[]), \
                     patch.object(views, 'render', side_effect=lambda req, template, ctx: ctx):
                    contracts.objects.for_tenant.return_value.filter.return_value = base
                    paginator.return_value.get_page.return_value = page
                    context = inspect.unwrap(getattr(views, name))(request)
                self.assertEqual(filtering.call_args.kwargs['quality'], '')
                self.assertEqual(filtering.call_args.kwargs['query'], 'steel')
                self.assertEqual(filtering.call_args.kwargs['ordering'], 'name')
                self.assertEqual(context['filters']['ownership'], 'PUBLIC')

    def test_contract_view_ignores_old_quality_url_and_preserves_tenant(self):
        from contracts import views
        request = RequestFactory().get('/contracts/?quality=REVIEW_REQUIRED&q=abc&direction=SALE&ownership=PUBLIC&date=active')
        request.tenant = self.tenant
        with patch.object(views, 'build_contract_list', return_value={'queryset': []}) as builder, \
             patch.object(views, 'enrich_contracts', return_value=[]), \
             patch.object(views, 'render', return_value='rendered'):
            self.assertEqual(inspect.unwrap(views.contract_list)(request), 'rendered')
        self.assertIs(builder.call_args.args[0], self.tenant)
        self.assertEqual(builder.call_args.kwargs['quality'], '')
        self.assertEqual(builder.call_args.kwargs['ownership'], 'PUBLIC')
        self.assertEqual(builder.call_args.kwargs['date_state'], 'active')
        self.assertEqual(builder.call_args.kwargs['direction'], 'SALE')
