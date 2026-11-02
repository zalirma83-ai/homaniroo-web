from decimal import Decimal
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

from common.templatetags.jalali import fa_number, ui_label


class DisplayLabelsTests(SimpleTestCase):
    def test_rec_variants(self):
        for value in ["برق رک یا REC", "برق رک (REC)", "برق رک/REC", "برق رک / REC", "برق رک - REC", "رک یا REC", "برق رك یا rec"]:
            with self.subTest(value=value):
                self.assertEqual(ui_label(value), "برق رک")

    def test_call_result(self):
        self.assertEqual(ui_label("قرارداد دارن"), "قرارداد دارند")

    def test_idempotence(self):
        for value in ["برق رک", "قرارداد دارند", "برق پایه"]:
            self.assertEqual(ui_label(ui_label(value)), value)

    def test_other_values_unchanged(self):
        for value in ["REC", "SALE", "LEAD", "ACTIVE", "CN-1405-01", "برق آزاد", "شرح: قرارداد دارن", "برق رک و پایه", "REC_TEST"]:
            with self.subTest(value=value):
                self.assertEqual(ui_label(value), value)

    def test_empty(self):
        self.assertEqual(ui_label(None), "")
        self.assertEqual(ui_label(""), "")

    def test_template_escaping(self):
        self.assertEqual(Template('{{ value|ui_label }}').render(Context({'value': '<b>متن</b>'})), '&lt;b&gt;متن&lt;/b&gt;')

    def test_energy_option_value_preserved(self):
        html = Template('<option value="{{ id }}">{{ label|ui_label }}</option>').render(Context({'id': 17, 'label': 'برق رک یا REC'}))
        self.assertEqual(html, '<option value="17">برق رک</option>')

    def test_negative_number_display_retained(self):
        self.assertEqual(fa_number(Decimal('-659500.25')), '(۶۵۹۵۰۰.۲۵)')

    def test_current_templates_use_display_filter(self):
        root = Path(__file__).resolve().parent.parent / 'templates'
        self.assertIn('item.energy_type_name_fa|ui_label', (root/'contracts/contract_list.html').read_text(encoding='utf-8'))
        self.assertIn('item.interaction_status|default:"بدون وضعیت"|ui_label', (root/'reports/report_center.html').read_text(encoding='utf-8'))
        queue = (root/'interactions/work_queue.html').read_text(encoding='utf-8')
        self.assertIn('value="LEAD"', queue)
        self.assertIn('مشترک بالقوه', queue)
        self.assertNotIn('مشتری بالقوه', queue)
