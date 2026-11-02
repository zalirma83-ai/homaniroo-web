"""E07: remove only the obsolete partner detail explanation."""
from common import test_e06_partner_note_ui as e06
from django.test import SimpleTestCase

NOTICE = 'این صفحه فقط برای مشاهده است. دسته\u200cبندی همکار، وضعیت پرونده مشتری را تغییر نمی\u200cدهد و پرونده مشتری جدیدی ایجاد نمی\u200cکند.'

class E07PartnerDetailNoticeTests(SimpleTestCase):
    setUp = e06.E06PartnerNoteUITests.setUp
    render_page = e06.E06PartnerNoteUITests.render_page

    def test_notice_absent_for_editor_and_reader(self):
        for editable in (True, False):
            with self.subTest(editable=editable):
                html = self.render_page(can_edit_note=editable)
                self.assertNotIn(NOTICE, html)
                self.assertNotIn('این صفحه فقط برای مشاهده است.', html)
                self.assertIn('مشخصات تجاری', html)
                self.assertIn('شناسه ملی ارائه‌شده (بدون اعتبارسنجی)', html)
                self.assertIn('id="operator-note"', html)
                self.assertEqual('ذخیره یادداشت' in html, editable)
                panel = html.split('<section id="operator-note"', 1)[1].split('</section>', 1)[0]
                self.assertEqual('csrfmiddlewaretoken' in panel, editable)

    def test_partner_and_customer_links_remain(self):
        self.partner.customer_id = 123
        html = self.render_page()
        self.assertIn('/customers/partners/', html)
        self.assertIn('/customers/profile/123/', html)
        self.assertIn('همکار آزمایشی', html)
        self.assertNotIn(self.partner.notes, html)
