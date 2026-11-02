from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import CoreUser, Role, Tenant


class CompanyUserCreateForm(forms.Form):
    username = forms.RegexField(
        label="نام کاربری",
        regex=r"^[A-Za-z0-9_.-]+$",
        max_length=150,
        help_text="فقط حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط",
    )
    full_name = forms.CharField(label="نام کامل", max_length=200)
    email = forms.EmailField(label="ایمیل", required=False)
    tenant = forms.ModelChoiceField(
        label="شرکت",
        queryset=Tenant.objects.none(),
    )
    role = forms.ModelChoiceField(
        label="نقش",
        queryset=Role.objects.none(),
    )
    password1 = forms.CharField(
        label="رمز عبور",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="تکرار رمز عبور",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = Tenant.objects.filter(
            status="ACTIVE"
        ).order_by("display_name")
        self.fields["role"].queryset = Role.objects.exclude(
            role_code="SUPERADMIN"
        ).order_by("role_id")

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        User = get_user_model()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("این نام کاربری قبلاً ثبت شده است.")
        if CoreUser.objects.filter(username__iexact=username).exists():
            raise ValidationError("این نام کاربری در اطلاعات تجاری موجود است.")
        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "تکرار رمز عبور یکسان نیست.")
        if password1:
            user = get_user_model()(username=cleaned.get("username", ""))
            try:
                validate_password(password1, user=user)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned


class CompanyUserUpdateForm(forms.Form):
    full_name = forms.CharField(label="نام کامل", max_length=200)
    email = forms.EmailField(label="ایمیل", required=False)
    tenant = forms.ModelChoiceField(
        label="شرکت این ویرایش",
        disabled=True,
        help_text="فقط نقش همین شرکت ویرایش می‌شود؛ برای شرکت دیگر از ردیف همان شرکت وارد شوید.",
        queryset=Tenant.objects.none(),
    )
    role = forms.ModelChoiceField(
        label="نقش",
        queryset=Role.objects.none(),
    )
    account_status = forms.ChoiceField(
        label="وضعیت کلی حساب ورود",
        help_text="غیرفعال یا قفل‌شدن، ورود به همهٔ شرکت‌ها را قطع می‌کند؛ عضویت‌های شرکتی را حذف یا فعال نمی‌کند.",
        choices=(("ACTIVE", "فعال"), ("INACTIVE", "غیرفعال"), ("LOCKED", "قفل‌شده")),
    )

    edit_token = forms.CharField(widget=forms.HiddenInput, max_length=2048)

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = Tenant.objects.filter(
            pk=getattr(tenant, "tenant_id", None)
        ).order_by("display_name")
        self.fields["role"].queryset = Role.objects.exclude(
            role_code="SUPERADMIN"
        ).order_by("role_id")
