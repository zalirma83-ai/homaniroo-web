from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect


class SuperadminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise ValidationError("ورود به پنل مدیریت فقط برای سوپرادمین مجاز است.", code="invalid_login")


class SuperadminAdminSite(AdminSite):
    """Staff status or model permissions cannot bypass credential policy."""
    login_form = SuperadminAuthenticationForm

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser

    def password_change(self, request, extra_context=None):
        return redirect("accounts:password_change")
