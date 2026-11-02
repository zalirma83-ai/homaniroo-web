from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "core_user_id", "must_change_password", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email")
    list_filter = ("must_change_password",)
    readonly_fields = ("must_change_password",)


class CredentialSafeUserChangeForm(UserChangeForm):
    password = None


class SuperadminUserAdmin(UserAdmin):
    """All credential edits use the same synchronized, audited service."""
    form = CredentialSafeUserChangeForm
    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + (("username", "login_credentials") if obj else ())

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return super().get_fieldsets(request, obj)
        result = []
        for title, options in super().get_fieldsets(request, obj):
            options = dict(options)
            options["fields"] = tuple("login_credentials" if field == "password" else field for field in options["fields"])
            result.append((title, options))
        return result

    @admin.display(description="اطلاعات ورود")
    def login_credentials(self, obj):
        return format_html('<a href="{}">ویرایش نام کاربری و تعیین رمز جدید</a>', reverse("accounts:user_credentials", args=[obj.pk]))

    def user_change_password(self, request, id, form_url=""):
        return redirect("accounts:user_credentials", user_id=id)

    def add_view(self, request, form_url="", extra_context=None):
        return redirect("tenants:user_create")


User = get_user_model()
if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, SuperadminUserAdmin)
