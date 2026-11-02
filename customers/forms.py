import re

import jdatetime
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone


PERSIAN_TO_ENGLISH = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _clean_optional(value):
    return (value or "").strip()


class ProspectiveCustomerCreateForm(forms.Form):
    official_name = forms.CharField(label="نام واحد صنعتی یا شرکت", max_length=300)
    national_id = forms.CharField(label="شناسه ملی", max_length=50, required=False)
    economic_code = forms.CharField(label="کد اقتصادی", max_length=50, required=False)
    postal_code = forms.CharField(label="کد پستی", max_length=20, required=False)
    address = forms.CharField(label="نشانی", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    contact_person = forms.CharField(label="نام مسئول انرژی", max_length=200, required=False)
    mobile_number = forms.CharField(label="شماره همراه", max_length=30, required=False)
    phone_number = forms.CharField(label="شماره تلفن", max_length=30, required=False)
    email = forms.EmailField(label="ایمیل", required=False)
    website = forms.URLField(label="وب‌سایت", required=False, assume_scheme="https")

    def clean_official_name(self):
        value = self.cleaned_data["official_name"].strip()
        if len(value) < 2:
            raise ValidationError("نام واحد صنعتی یا شرکت باید حداقل دو نویسه باشد.")
        return value

    def clean(self):
        cleaned = super().clean()
        channels = (
            cleaned.get("mobile_number"), cleaned.get("phone_number"),
            cleaned.get("email"), cleaned.get("website"),
        )
        if not any(_clean_optional(v) for v in channels):
            raise ValidationError("حداقل یک راه ارتباطی شامل همراه، تلفن، ایمیل یا وب‌سایت وارد کنید.")
        return cleaned


class ProspectiveCustomerIdentityForm(forms.Form):
    official_name = forms.CharField(label="نام واحد صنعتی یا شرکت", max_length=300)
    national_id = forms.CharField(label="شناسه ملی", max_length=50, required=False)
    economic_code = forms.CharField(label="کد اقتصادی", max_length=50, required=False)
    postal_code = forms.CharField(label="کد پستی", max_length=20, required=False)
    address = forms.CharField(label="نشانی", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_official_name(self):
        value = self.cleaned_data["official_name"].strip()
        if len(value) < 2:
            raise ValidationError("نام واحد صنعتی یا شرکت باید حداقل دو نویسه باشد.")
        return value


class CustomerContactForm(forms.Form):
    contact_person = forms.CharField(label="نام مسئول انرژی", max_length=200, required=False)
    mobile_number = forms.CharField(label="شماره همراه", max_length=30, required=False)
    phone_number = forms.CharField(label="شماره تلفن", max_length=30, required=False)
    email = forms.EmailField(label="ایمیل", required=False)
    website = forms.URLField(label="وب‌سایت", required=False, assume_scheme="https")
    is_primary = forms.BooleanField(label="اطلاعات تماس اصلی", required=False)

    def clean(self):
        cleaned = super().clean()
        values = [cleaned.get(name) for name in (
            "contact_person", "mobile_number", "phone_number", "email", "website"
        )]
        if not any(_clean_optional(v) for v in values):
            raise ValidationError("حداقل یکی از اطلاعات تماس را وارد کنید.")
        return cleaned


class InteractionForm(forms.Form):
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
        ("OTHER", "سایر"),
    )
    interaction_type = forms.ChoiceField(label="نوع ارتباط", choices=INTERACTION_TYPES)
    interaction_status = forms.ChoiceField(label="نتیجه", choices=RESULT_CHOICES)
    notes = forms.CharField(label="شرح مذاکره یا تماس", widget=forms.Textarea(attrs={"rows": 4}))
    follow_up_jalali = forms.CharField(
        label="زمان پیگیری بعدی", required=False,
        help_text="نمونه: ۱۴۰۵/۰۷/۱۵ ۱۰:۳۰",
        widget=forms.TextInput(attrs={"placeholder": "۱۴۰۵/۰۷/۱۵ ۱۰:۳۰", "dir": "ltr", "data-jalali-picker": "datetime"}),
    )
    follow_up_reason = forms.CharField(label="دلیل پیگیری", required=False, max_length=500)

    def clean_follow_up_jalali(self):
        raw = (self.cleaned_data.get("follow_up_jalali") or "").strip()
        if not raw:
            return None
        normalized = raw.translate(PERSIAN_TO_ENGLISH)
        normalized = re.sub(r"\s+", " ", normalized)
        try:
            parsed = jdatetime.datetime.strptime(normalized, "%Y/%m/%d %H:%M").togregorian()
        except ValueError as exc:
            raise ValidationError("تاریخ را با الگوی ۱۴۰۵/۰۷/۱۵ ۱۰:۳۰ وارد کنید.") from exc
        return timezone.make_aware(parsed, timezone.get_current_timezone())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("interaction_status") == "FOLLOW_UP_REQUIRED" and not cleaned.get("follow_up_jalali"):
            self.add_error("follow_up_jalali", "برای این نتیجه، زمان پیگیری بعدی الزامی است.")
        return cleaned

DORMANT_REASON_CHOICES = (
    ("CONTRACT_ENDED", "پایان قرارداد یا دوره همکاری"),
    ("NO_INTEREST", "عدم تمایل به ادامه همکاری"),
    ("NO_RESPONSE", "عدم پاسخ‌گویی یا قطع ارتباط"),
    ("OTHER_PROVIDER", "همکاری با تأمین‌کننده دیگر"),
    ("ACTIVITY_SUSPENDED", "توقف یا تعلیق فعالیت مشترک"),
    ("FINANCIAL_ISSUE", "مسائل مالی یا بدهی"),
    ("OTHER", "سایر موارد"),
)


class DormantTransitionForm(forms.Form):
    reason_code = forms.ChoiceField(
        label="علت توقف همکاری", choices=DORMANT_REASON_CHOICES
    )
    reason_note = forms.CharField(
        label="توضیحات تکمیلی",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("reason_code") == "OTHER" and not _clean_optional(
            cleaned.get("reason_note")
        ):
            self.add_error(
                "reason_note", "برای گزینه سایر، درج توضیحات تکمیلی الزامی است."
            )
        return cleaned


class DormantRestoreForm(forms.Form):
    target_status = forms.ChoiceField(
        label="وضعیت مقصد",
        choices=(("LEAD", "بازگشت به مشترک بالقوه"), ("ACTIVE", "بازگشت به مشترک فعال")),
        widget=forms.RadioSelect,
    )
    reason_note = forms.CharField(
        label="توضیح بازگشت",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
