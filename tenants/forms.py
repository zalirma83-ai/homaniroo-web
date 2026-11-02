import re

from django import forms
from django.core.exceptions import ValidationError

from .models import Tenant
from .services import get_allowed_tenants


class TenantSelectionForm(forms.Form):
    tenant = forms.ModelChoiceField(
        label="شرکت فعال",
        queryset=Tenant.objects.none(),
        empty_label="یک شرکت را انتخاب کنید",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = get_allowed_tenants(user)


class TenantForm(forms.ModelForm):
    status = forms.ChoiceField(
        label="وضعیت",
        choices=(("ACTIVE", "فعال"), ("INACTIVE", "غیرفعال")),
    )

    class Meta:
        model = Tenant
        fields = ("tenant_code", "legal_name", "display_name", "status")
        labels = {
            "tenant_code": "کد شرکت",
            "legal_name": "نام قانونی",
            "display_name": "نام نمایشی",
        }
        help_texts = {
            "tenant_code": "فقط حروف انگلیسی بزرگ، عدد و زیرخط؛ مثال: HOMANIROO",
        }

    def clean_tenant_code(self):
        code = self.cleaned_data["tenant_code"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValidationError("کد شرکت فقط می‌تواند شامل حروف انگلیسی، عدد و زیرخط باشد.")

        duplicate = Tenant.objects.filter(tenant_code__iexact=code)
        if self.instance and self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError("این کد شرکت قبلاً ثبت شده است.")
        return code
