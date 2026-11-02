from datetime import date, datetime
import re

import jdatetime
from django import template
from django.utils import timezone

register = template.Library()

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# Only a complete numeric value qualifies; embedded minus signs stay unchanged.
_NEGATIVE_NUMBER = re.compile(
    r"[-−](?P<magnitude>"
    r"(?:(?:\d{1,3}(?:[,٬\u00a0\u202f]\d{3})+|\d+)(?:[.٫]\d+)?|[.٫]\d+)"
    r"(?:[eE][+-]?\d+)?)"
)


def _persian_digits(value):
    return str(value).translate(PERSIAN_DIGITS)


@register.filter
def jalali_date(value):
    """نمایش تاریخ میلادی ذخیره‌شده به شکل شمسی."""
    if not value:
        return ""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        result = jdatetime.datetime.fromgregorian(datetime=value).strftime("%Y/%m/%d")
        return _persian_digits(result)
    if isinstance(value, date):
        result = jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
        return _persian_digits(result)
    return value


@register.filter
def jalali_datetime(value):
    """نمایش تاریخ و ساعت به وقت ایران و تقویم شمسی."""
    if not value:
        return ""
    if not isinstance(value, datetime):
        return jalali_date(value)
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    result = jdatetime.datetime.fromgregorian(datetime=value).strftime(
        "%Y/%m/%d - %H:%M"
    )
    return _persian_digits(result)


@register.filter
def fa_number(value):
    """نمایش ارقام فارسی و اعداد منفی داخل پرانتز، بدون تغییر مقدار اصلی."""
    if value is None:
        return "—"
    display = _persian_digits(value)
    negative = _NEGATIVE_NUMBER.fullmatch(display.strip())
    if negative:
        return f"({negative.group('magnitude')})"
    return display


@register.filter
def has_any_role(user, roles_csv):
    """بررسی مجوز نمایشی عملیات بر اساس نقش‌های کاربر."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    roles = [role.strip() for role in str(roles_csv).split(",") if role.strip()]
    return user.groups.filter(name__in=roles).exists()


@register.filter
def ui_label(value):
    """Normalize requested display labels only; never change stored values."""
    if value is None:
        return ""
    text = str(value)
    lookup = text.strip().replace("ك", "ک").replace("ي", "ی")
    if lookup == "قرارداد دارن":
        return "قرارداد دارند"
    if re.fullmatch(r"(?:برق\s+)?رک(?:\s*(?:یا|/|[-ـ])?\s*\(?REC\)?)?", lookup, re.IGNORECASE):
        return "برق رک"
    return text


@register.filter
def has_tenant_roles(request, roles_csv):
    """Display operational controls using the same active-tenant rule as the view."""
    from accounts.access import has_tenant_role
    roles = tuple(role.strip() for role in str(roles_csv).split(",") if role.strip())
    return has_tenant_role(getattr(request, "user", None), getattr(request, "tenant", None), roles)
