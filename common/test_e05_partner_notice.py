"""E05: remove only the explanatory ID-validation notice from the partner list."""
from pathlib import Path
from types import SimpleNamespace as N

from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase


NOTICE = 'شناسه‌های ارائه‌شده عیناً نمایش داده می‌شوند؛ نمایش آن‌ها به معنی اعتبارسنجی شناسه ملی نیست.'


class E05PartnerNoticeTests(SimpleTestCase):
    def setUp(self):
        self.user = N(is_authenticated=True, is_superuser=True, is_staff=True,
                      username='qa', get_full_name=lambda: 'کاربر آزمایشی')
        self.request = RequestFactory().get('/customers/partners/?q=test&ownership=UNKNOWN')
        self.request.user = self.user
        self.request.tenant = N(tenant_id=1, display_name='شرکت آزمایشی')
        self.request.tenant_choices = [self.request.tenant]

    def render_page(self, partners):
        return render_to_string('customers/partner_list.html', {
            'request': self.request, 'user': self.user, 'query': 'test',
            'ownership_filter': 'UNKNOWN', 'total': len(partners),
            'page': Paginator(partners, 25).get_page(1),
        })

    def partner(self, national_id):
        return N(profile_id=7, display_name='همکار آزمایشی',
                 industrial_unit=N(official_name='همکار آزمایشی'),
                 provided_national_id=national_id, ownership_code='UNKNOWN')

    def test_notice_is_removed_from_source_and_empty_page(self):
        source = (Path(__file__).resolve().parent.parent / 'templates/customers/partner_list.html').read_text(encoding='utf-8-sig')
        self.assertNotIn(NOTICE, source)
        html = self.render_page([])
        self.assertNotIn(NOTICE, html)
        self.assertIn('colspan="4"', html)
        self.assertIn('همکار تأییدشده‌ای مطابق این جست‌وجو', html)

    def test_original_identifier_and_detail_link_are_preserved(self):
        item = self.partner('00000012345')
        html = self.render_page([item])
        self.assertNotIn(NOTICE, html)
        self.assertIn('00000012345', html)
        self.assertEqual(item.provided_national_id, '00000012345')
        self.assertIn('شناسه ملی ارائه‌شده', html)
        self.assertIn('/customers/partners/7/', html)
        self.assertIn('مشاهده مشخصات', html)
        self.assertIn('name="q"', html)
        self.assertIn('name="ownership"', html)

    def test_missing_identifier_is_not_fabricated(self):
        item = self.partner(None)
        html = self.render_page([item])
        self.assertIn('<span dir="ltr">—</span>', html)
        self.assertIsNone(item.provided_national_id)

    def test_identifier_is_escaped_without_being_rewritten(self):
        value = '<script>example</script>'
        item = self.partner(value)
        html = self.render_page([item])
        self.assertIn('&lt;script&gt;example&lt;/script&gt;', html)
        self.assertNotIn(value, html)
        self.assertEqual(item.provided_national_id, value)
