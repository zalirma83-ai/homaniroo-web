from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from customers.forms import InteractionForm
from interactions.forms import InteractionCreateForm, FollowUpRescheduleForm, parse_jalali_datetime


class FollowupPickerTests(SimpleTestCase):
    def test_all_three_widgets_marked(self):
        for form, field in [(InteractionForm, 'follow_up_jalali'), (InteractionCreateForm, 'follow_up_jalali'), (FollowUpRescheduleForm, 'due_jalali')]:
            with self.subTest(form=form.__name__):
                self.assertEqual(form.base_fields[field].widget.attrs['data-jalali-picker'], 'datetime')
                self.assertNotIn('(شمسی)', form.base_fields[field].label)

    def test_selected_datetime_converts_correctly(self):
        with timezone.override('Asia/Tehran'):
            actual = parse_jalali_datetime('1405/07/25 10:30')
        self.assertEqual(actual, datetime(2026, 10, 17, 10, 30, tzinfo=ZoneInfo('Asia/Tehran')))

    def test_persian_digits_remain_accepted(self):
        self.assertEqual(parse_jalali_datetime('۱۴۰۵/۰۷/۲۵ ۱۰:۳۰'), parse_jalali_datetime('1405/07/25 10:30'))

    def test_leap_day_valid(self):
        self.assertEqual(parse_jalali_datetime('1399/12/30 23:59').date().isoformat(), '2021-03-20')

    def test_nonleap_day_rejected(self):
        with self.assertRaises(ValidationError):
            parse_jalali_datetime('1400/12/30 10:30')

    def test_invalid_time_rejected(self):
        with self.assertRaises(ValidationError):
            parse_jalali_datetime('1405/07/25 24:00')

    def test_optional_value_remains_optional(self):
        self.assertIsNone(parse_jalali_datetime(''))
        self.assertIsNone(InteractionForm({'interaction_type': 'CALL', 'interaction_status': 'OTHER', 'notes': 'متن'}).fields['follow_up_jalali'].clean('') or None)

    def test_reschedule_required_and_valid(self):
        self.assertFalse(FollowUpRescheduleForm({'due_jalali': ''}).is_valid())
        form = FollowUpRescheduleForm({'due_jalali': '1405/07/25 10:30'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(timezone.is_aware(form.cleaned_data['due_jalali']))

    def test_customer_form_uses_same_date_semantics(self):
        form = InteractionForm({'interaction_type': 'CALL', 'interaction_status': 'OTHER', 'notes': 'متن', 'follow_up_jalali': '1405/07/25 10:30'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['follow_up_jalali'], parse_jalali_datetime('1405/07/25 10:30'))

    def test_calendar_assets_loaded_once(self):
        root = Path(__file__).resolve().parent.parent
        base = (root/'templates/base.html').read_text(encoding='utf-8')
        self.assertEqual(base.count('css/jalali-picker.css'), 1)
        self.assertEqual(base.count('js/jalali-picker.js'), 1)
        js = (root/'static/js/jalali-picker.js').read_text(encoding='utf-8')
        self.assertNotIn('fetch(', js)
        self.assertNotIn('innerHTML', js)
