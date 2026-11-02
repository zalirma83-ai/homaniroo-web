from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm


class PersianAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="نام کاربری",
        widget=forms.TextInput(
            attrs={"autofocus": True, "autocomplete": "username"}
        ),
    )
    password = forms.CharField(
        label="رمز عبور",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class PersianPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="رمز عبور فعلی",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    new_password1 = forms.CharField(
        label="رمز عبور جدید",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="تکرار رمز عبور جدید",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )


class CredentialManagementForm(forms.Form):
    username = forms.RegexField(
        label="نام کاربری", regex=r"\A[A-Za-z0-9_.-]{1,150}\Z", max_length=150,
        help_text="حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    new_password1 = forms.CharField(
        label="رمز عبور جدید", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="تکرار رمز عبور جدید", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, target_user, **kwargs):
        self.target_user = target_user
        kwargs.setdefault("initial", {"username": target_user.username})
        super().__init__(*args, **kwargs)

    def clean_username(self):
        from .credential_services import validate_login_username
        profile = getattr(self.target_user, "profile", None)
        return validate_login_username(
            self.cleaned_data["username"], user_id=self.target_user.pk,
            core_user_id=profile.core_user_id if profile else None,
        )

    def clean(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        cleaned = super().clean()
        password1 = cleaned.get("new_password1", "")
        password2 = cleaned.get("new_password2", "")
        if password1 != password2:
            self.add_error("new_password2", "تکرار رمز عبور یکسان نیست.")
        if password1:
            # Validate against the proposed username, without changing the live object.
            candidate = get_user_model()(
                username=cleaned.get("username", self.target_user.username),
                first_name=self.target_user.first_name,
                last_name=self.target_user.last_name, email=self.target_user.email,
            )
            try:
                validate_password(password1, user=candidate)
            except ValidationError as error:
                self.add_error("new_password1", error)
        return cleaned
