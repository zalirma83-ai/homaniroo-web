import re

import jdatetime
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from customers.models import TenantCustomer


PERSIAN_TO_ENGLISH = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

INTERACTION_TYPES = (
    ("CALL", "تماس تلفنی"),
    ("MEETING", "جلسه"),
    ("MESSAGE", "پیام‌رسان"),
    ("EMAIL", "ایمیل"),
    ("OTHER", "سایر"),
)

RESULT_CHOICES = (
    ("INITIAL_CONTACT", "تماس اولیه انجام شد"),
    ("NO_ANSWER", "پاسخ نداد"),
    ("INTERESTED", "علاقه‌مند به ادامه مذاکره"),
    ("FOLLOW_UP_REQUIRED", "نیازمند پیگیری مجدد"),
    ("NOT_INTERESTED", "عدم تمایل"),
    ("WRONG_NUMBER", "شماره اشتباه"),
    ("CONTRACTED_ELSEWHERE", "دارای قرارداد با مجموعه دیگر"),
    ("COMPLETED", "پیگیری انجام شد"),
    ("OTHER", "سایر"),
)


def parse_jalali_datetime(value):
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = re.sub(r"\s+", " ", raw.translate(PERSIAN_TO_ENGLISH))
    try:
        parsed = jdatetime.datetime.strptime(
            normalized, "%Y/%m/%d %H:%M"
        ).togregorian()
    except ValueError as exc:
        raise ValidationError(
            "تاریخ را با الگوی ۱۴۰۵/۰۷/۱۵ ۱۰:۳۰ وارد کنید."
        ) from exc
    return timezone.make_aware(parsed, timezone.get_current_timezone())


class InteractionCreateForm(forms.Form):
    tenant_customer_id = forms.ChoiceField(label="مشتری")
    interaction_type = forms.ChoiceField(
        label="نوع ارتباط", choices=INTERACTION_TYPES
    )
    interaction_status = forms.ChoiceField(
        label="نتیجه تماس یا ارتباط", choices=RESULT_CHOICES
    )
    notes = forms.CharField(
        label="شرح تماس یا مذاکره",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    follow_up_jalali = forms.CharField(
        label="زمان پیگیری بعدی",
        required=False,
        help_text="نمونه: ۱۴۰۵/۰۷/۱۵ ۱۰:۳۰",
        widget=forms.TextInput(
            attrs={"placeholder": "۱۴۰۵/۰۷/۱۵ ۱۰:۳۰", "dir": "ltr", "data-jalali-picker": "datetime"}
        ),
    )
    follow_up_reason = forms.CharField(
        label="دلیل پیگیری",
        required=False,
        max_length=500,
    )

    def __init__(self, *args, tenant=None, fixed_customer_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = TenantCustomer.objects.none()
        if tenant is not None:
            queryset = (
                TenantCustomer.objects.for_tenant(tenant)
                .select_related("industrial_unit")
                .order_by("industrial_unit__official_name", "tenant_customer_id")
            )
        status_labels = {
            "ACTIVE": "فعال",
            "LEAD": "بالقوه",
            "DORMANT": "راکد",
        }
        self.fields["tenant_customer_id"].choices = [
            (
                str(item.tenant_customer_id),
                f"{item.industrial_unit.official_name} ـ "
                f"{status_labels.get(item.customer_status, item.customer_status)}",
            )
            for item in queryset
        ]
        if fixed_customer_id is not None:
            fixed_customer_id = str(fixed_customer_id)
            fixed_choices = [
                choice
                for choice in self.fields["tenant_customer_id"].choices
                if choice[0] == fixed_customer_id
            ]
            if fixed_choices:
                self.fields["tenant_customer_id"].choices = fixed_choices
                self.fields["tenant_customer_id"].initial = fixed_customer_id
                self.fields["tenant_customer_id"].widget = forms.HiddenInput()

    def clean_follow_up_jalali(self):
        return parse_jalali_datetime(self.cleaned_data.get("follow_up_jalali"))

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("interaction_status") == "FOLLOW_UP_REQUIRED"
            and not cleaned.get("follow_up_jalali")
        ):
            self.add_error(
                "follow_up_jalali",
                "برای نتیجه نیازمند پیگیری، زمان پیگیری بعدی الزامی است.",
            )
        if cleaned.get("interaction_status") == "OTHER" and not (
            cleaned.get("notes") or ""
        ).strip():
            self.add_error("notes", "برای نتیجه سایر، درج توضیحات الزامی است.")
        return cleaned


class FollowUpActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(("DONE", "انجام شد"), ("CANCELLED", "لغو شد")),
        widget=forms.HiddenInput(),
    )
    note = forms.CharField(required=False, max_length=1000)


class FollowUpRescheduleForm(forms.Form):
    due_jalali = forms.CharField(
        label="زمان جدید پیگیری",
        help_text="نمونه: ۱۴۰۵/۰۷/۱۵ ۱۰:۳۰",
        widget=forms.TextInput(
            attrs={"placeholder": "۱۴۰۵/۰۷/۱۵ ۱۰:۳۰", "dir": "ltr", "data-jalali-picker": "datetime"}
        ),
    )
    reason = forms.CharField(
        label="دلیل یا توضیح پیگیری",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_due_jalali(self):
        return parse_jalali_datetime(self.cleaned_data.get("due_jalali"))


class AlertStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            ("OPEN", "باز"),
            ("SEEN", "مشاهده‌شده"),
            ("COMPLETED", "رسیدگی‌شده"),
            ("DISMISSED", "کنار گذاشته‌شده"),
        ),
        widget=forms.HiddenInput(),
    )
