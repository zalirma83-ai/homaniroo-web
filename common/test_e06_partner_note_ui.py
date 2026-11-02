"""E06 presentation and validation checks; no database required."""
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as N

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from customers.partner_note_forms import PartnerNoteForm
from customers.partner_notes import clean_note_body


class Textarea(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inside = False
        self.text = ''
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag == 'textarea':
            self.inside = True
    def handle_endtag(self, tag):
        if tag == 'textarea':
            self.inside = False
    def handle_data(self, data):
        if self.inside:
            self.text += data


class E06PartnerNoteUITests(SimpleTestCase):
    def setUp(self):
        self.user = N(is_authenticated=True, is_superuser=True, is_staff=True,
                      username='qa', get_full_name=lambda:'آزمایش')
        self.request = RequestFactory().get('/customers/partners/7/')
        self.request.user = self.user
        self.request.tenant = N(tenant_id=1, display_name='شرکت آزمایشی')
        self.request.tenant_choices = [self.request.tenant]
        self.partner = N(profile_id=7, display_name='همکار آزمایشی', customer_id=None,
                         industrial_unit=N(industrial_unit_id=3, official_name='همکار آزمایشی'),
                         ownership=None, notes='HISTORICAL_NOTE_MUST_NOT_BE_EDITED')

    def render_page(self, **extra):
        return render_to_string('customers/partner_detail.html', {
            'request':self.request, 'user':self.user, 'csrf_token':'qa',
            'partner':self.partner, 'operator_note':None, 'can_edit_note':False,
            'note_form':PartnerNoteForm(initial={'body':'','version':0}), **extra,
        })

    def test_empty_editor_has_no_imported_note_or_synthetic_placeholder(self):
        html = self.render_page(can_edit_note=True)
        self.assertEqual(Textarea(html).text.strip(), '')
        self.assertNotIn(self.partner.notes, html)
        self.assertNotIn('اطلاعات این مشتری یا شرکت در دیتابیس اولیه موجود نبود.', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('name="version"', html)
        self.assertIn('maxlength="10000"', html)

    def test_empty_readonly_panel_has_no_edit_form(self):
        html = self.render_page()
        self.assertNotIn('ذخیره یادداشت', html)
        self.assertNotIn('<textarea', html)
        self.assertNotIn(self.partner.notes, html)
        self.assertIn('id="operator-note"', html)

    def test_readonly_content_is_escaped_and_newlines_are_displayed(self):
        note=N(body='<script>bad</script>\nخط دوم', updated_at=datetime(2026,10,27,tzinfo=timezone.utc), updated_by_name='اپراتور')
        html=self.render_page(operator_note=note)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', html)
        self.assertNotIn('<script>bad</script>', html)
        self.assertIn('خط دوم', html)
        self.assertIn('آخرین ویرایش', html)

    def test_form_normalizes_newlines_but_keeps_unicode_and_zero_text(self):
        form=PartnerNoteForm({'body':'  سطر اول\r\n۰  ', 'version':'0'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['body'], 'سطر اول\n۰')
        self.assertEqual(form.cleaned_data['version'], 0)
        self.assertTrue(PartnerNoteForm({'body':'', 'version':0}).is_valid())

    def test_form_and_service_reject_invalid_text_and_version(self):
        for body in ('x'*10001, 'bad\x00text'):
            with self.subTest(length=len(body)):
                self.assertFalse(PartnerNoteForm({'body':body, 'version':0}).is_valid())
                with self.assertRaises(ValidationError):
                    clean_note_body(body)
        for value in ('bad', -1, ''):
            with self.subTest(version=value):
                self.assertFalse(PartnerNoteForm({'body':'text','version':value}).is_valid())

    def test_route_resolves_to_separate_post_endpoint(self):
        url=reverse('customers:partner_note_update', args=[7])
        self.assertEqual(url, '/customers/partners/7/note/')
        self.assertEqual(resolve(url).view_name, 'customers:partner_note_update')

    def test_e07_notice_removed_and_existing_identity_fields_remain(self):
        source=(Path(__file__).resolve().parent.parent/'templates/customers/partner_detail.html').read_text(encoding='utf-8-sig')
        self.assertNotIn('این صفحه فقط برای مشاهده است.', source)
        self.assertIn('partner.provided_national_id',source)
        self.assertIn('partner.industrial_unit.national_id',source)
        self.assertNotIn('{{ partner.notes',source)
