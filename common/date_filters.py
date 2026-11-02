"""Shared UI date validation. Database dates remain Gregorian and timezone-aware."""
import re
from datetime import date

import jdatetime
from django import forms
from django.shortcuts import render

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_DATE_ERROR = 'تاریخ معتبر وارد کنید؛ نمونه: ۱۴۰۵/۰۷/۲۵'


def parse_filter_date(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError(_DATE_ERROR)
    text = value.strip().translate(_DIGITS)
    if not text:
        return None
    match = re.fullmatch(r'(\d{4})/(\d{1,2})/(\d{1,2})', text)
    if match:
        year, month, day = map(int, match.groups())
        if not 1200 <= year <= 1600:
            raise ValueError(_DATE_ERROR)
        return jdatetime.date(year, month, day).togregorian()
    # Compatibility for existing bookmarks, direct links and server callers.
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) and int(text[:4]) >= 1700:
        result = date.fromisoformat(text)
        # The UI picker and server must cover the same supported year range.
        if not 1200 <= jdatetime.date.fromgregorian(date=result).year <= 1600:
            raise ValueError(_DATE_ERROR)
        return result
    raise ValueError(_DATE_ERROR)


def filter_date_display(value):
    return jdatetime.date.fromgregorian(date=value).strftime('%Y/%m/%d') if value else ''


class DateFilterRangeForm(forms.Form):
    date_from = forms.CharField(label='از تاریخ', required=False)
    date_to = forms.CharField(label='تا تاریخ', required=False)

    def _date(self, field):
        try:
            return parse_filter_date(self.cleaned_data.get(field))
        except (TypeError, ValueError, OverflowError):
            raise forms.ValidationError(_DATE_ERROR)

    def clean_date_from(self):
        return self._date('date_from')

    def clean_date_to(self):
        return self._date('date_to')

    def clean(self):
        data = super().clean()
        start, end = data.get('date_from'), data.get('date_to')
        if start and end and start > end:
            data['date_from'], data['date_to'] = end, start
        return data

    @property
    def iso_values(self):
        return {key: self.cleaned_data[key].isoformat() if self.cleaned_data.get(key) else '' for key in ('date_from', 'date_to')}

    @property
    def display_values(self):
        return {key: filter_date_display(self.cleaned_data.get(key)) for key in ('date_from', 'date_to')}


def date_filter_error_response(request, form, *, return_url, from_name, to_name):
    # Reject malformed filters rather than silently returning an unfiltered report/CSV.
    preserved = [(key, value) for key, values in request.GET.lists() for value in values if key not in (from_name, to_name, 'page')]
    errors = [f'{form.fields[key].label}: {message}' for key, messages in form.errors.items() for message in messages]
    return render(request, 'common/date_filter_error.html', {
        'date_errors': errors, 'return_url': return_url,
        'from_name': from_name, 'to_name': to_name,
        'date_from': form.data.get('date_from', ''), 'date_to': form.data.get('date_to', ''),
        'preserved_filters': preserved,
    }, status=400)
