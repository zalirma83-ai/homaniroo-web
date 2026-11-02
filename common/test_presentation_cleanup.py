from datetime import date
from pathlib import Path
from types import SimpleNamespace as N

from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from common.information_status import assess_contract
from contracts.models import Contract


class PresentationCleanupTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / 'templates'
        self.user = N(is_authenticated=True, is_superuser=True, is_staff=True,
                      username='qa', get_full_name=lambda: 'کاربر آزمایشی')
        self.request = RequestFactory().get('/')
        self.request.user = self.user
        self.request.tenant = N(tenant_id=1, display_name='شرکت آزمایشی')
        self.request.tenant_choices = [self.request.tenant]

    def source(self, path):
        return (self.root / path).read_text(encoding='utf-8')

    def render(self, path, **context):
        return render_to_string(path, {
            'request': self.request, 'user': self.user, 'csrf_token': 'qa',
            'page': Paginator([], 25).get_page(1), 'filters': {}, **context,
        })

    def test_only_aggregate_banner_markers_replace_banners(self):
        for path in ('customers/active_list.html', 'contracts/contract_list.html',
                     'energy/trading_dashboard.html', 'reports/report_center.html',
                     'interactions/alert_center.html'):
            with self.subTest(template=path):
                source = self.source(path)
                self.assertIn('presentation-cleanup:', source)
                self.assertNotIn('notice-box active-data-warning', source)

    def test_positive_counts_do_not_restore_aggregate_banners(self):
        samples = [
            ('customers/active_list.html', {'stats': {'contracts_missing_dates': 13}}),
            ('contracts/contract_list.html', {'stats': {'missing_dates': 13}}),
            ('energy/trading_dashboard.html', {'summary': {'incomplete': 13}}),
            ('reports/report_center.html', {'quality': {'customer_review': 13, 'contract_review': 17, 'energy_incomplete': 4}}),
            ('interactions/alert_center.html', {'quality': {'contracts_missing_end_date': 13, 'follow_ups_missing_date': 4}}),
        ]
        for path, context in samples:
            with self.subTest(template=path):
                html = self.render(path, **context)
                self.assertNotIn('active-data-warning', html)
                self.assertNotIn('presentation-cleanup:', html)  # Django comments are not displayed.

    def test_volume_units_and_incomplete_count_remain(self):
        html = self.render('energy/trading_dashboard.html', summary={
            'purchase': 123, 'sales': 456, 'incomplete': 7, 'rows': 10,
        })
        self.assertNotIn('مطابق داده مبنا', html)
        self.assertIn('<small>MW</small>', html)
        self.assertIn('ردیف ناقص تراز', html)
        self.assertIn('۷', html)
        self.assertIn('۱۲۳', html)
        self.assertIn('۴۵۶', html)

    def test_dashboard_keeps_metrics_and_unit(self):
        html = self.render('dashboard/home.html', dashboard_ready=True, dashboard_date=date(2026, 10, 17),
                           energy={'rows': 1, 'purchase_display': '123', 'sales_display': '456'},
                           quality={'customer_review': 7, 'contract_review': 4})
        self.assertNotIn('مطابق داده مبنا', html)
        self.assertIn('<small>مگاوات</small>', html)
        self.assertNotIn('موارد نیازمند رسیدگی', html)
        self.assertIn('هشدارهای عملیاتی باز', html)
        self.assertIn('۱۲۳', html)

    def test_real_alert_and_actions_remain(self):
        message = 'پیگیری این مشترک انجام نشده است.'
        row = {'item': N(alert_id=1, alert_at=None, alert_status='OPEN', message=message),
               'type_label': 'پیگیری مشتری', 'status_label': 'باز', 'customer': None,
               'severity_class': 'warning', 'severity_label': 'مهم'}
        html = self.render('interactions/alert_center.html', page=Paginator([row], 25).get_page(1))
        self.assertIn(message, html)
        self.assertIn('badge-warning', html)
        self.assertIn('مشاهده شد', html)
        self.assertIn('رسیدگی شد', html)
        self.assertIn('به‌روزرسانی هشدارها', html)

    def test_e04_pages_hide_quality_but_keep_internal_assessment(self):
        contract = Contract(data_quality_status='CLEAN')
        status = assess_contract(contract, [])
        html = render_to_string('partials/information_details.html', {'information': status})
        self.assertIn('موارد نیازمند رسیدگی', html)
        self.assertIn('نیازمند تکمیل', html)
        self.assertIn('تاریخ پایان قابل استفاده ثبت نشده', html)
        self.assertIn('ردیف انرژی ثبت نشده', html)
        for path in ('customers/active_list.html', 'contracts/contract_list.html'):
            with self.subTest(template=path):
                self.assertNotIn('partials/information_badges.html', self.source(path))
                self.assertNotIn('information.summary', self.source(path))

    def test_filters_exports_validation_and_security_remain(self):
        for path in ('customers/active_list.html', 'contracts/contract_list.html'):
            with self.subTest(template=path):
                source = self.source(path)
                self.assertNotIn('name="quality"', source)
                self.assertNotIn('value="INCOMPLETE"', source)
                self.assertNotIn('value="REVIEW_REQUIRED"', source)
        report = self.source('reports/report_center.html')
        self.assertIn('data-jalali-picker="date"', report)
        self.assertIn('reports:export_csv', report)
        self.assertIn('این بازه روی تاریخ ثبت‌شدهٔ تماس‌ها، پیگیری‌ها و هشدارها اعمال می‌شود.', report)
        self.assertIn('بازه تاریخ معتبر نیست.', self.source('common/date_filter_error.html'))
        self.assertIn('حریم خصوصی و امنیت', self.source('auditlog/audit_center.html'))
        self.assertIn('badge-{{ row.status_class }}', self.source('energy/trading_dashboard.html'))
