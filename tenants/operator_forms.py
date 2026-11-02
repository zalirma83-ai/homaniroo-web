from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.credential_services import validate_login_username
from accounts.forms import CredentialManagementForm


class OperatorCreateForm(forms.Form):
    username = forms.RegexField(label='نام کاربری', regex=r'\A[A-Za-z0-9_.-]{1,150}\Z', max_length=150,
                                help_text='حروف انگلیسی، عدد، نقطه، خط تیره و زیرخط')
    full_name = forms.CharField(label='نام کامل', max_length=200)
    email = forms.EmailField(label='ایمیل', required=False, max_length=254)
    password1 = forms.CharField(label='رمز عبور', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='تکرار رمز عبور', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def clean_username(self):
        return validate_login_username(self.cleaned_data['username'])

    def clean(self):
        data = super().clean()
        password = data.get('password1')
        if password and password != data.get('password2'):
            self.add_error('password2', 'تکرار رمز عبور یکسان نیست.')
        if password:
            candidate = get_user_model()(username=data.get('username', ''),
                first_name=data.get('full_name', '')[:150], email=data.get('email', ''))
            try:
                validate_password(password, user=candidate)
            except ValidationError as error:
                self.add_error('password1', error)
        return data


class OperatorEditForm(CredentialManagementForm):
    full_name = forms.CharField(label='نام کامل', max_length=200)
    account_status = forms.ChoiceField(label='وضعیت حساب', choices=(('ACTIVE', 'فعال'), ('INACTIVE', 'غیرفعال')))
    edit_token = forms.CharField(widget=forms.HiddenInput, max_length=2048)

    def __init__(self, *args, target_user, core, edit_token, **kwargs):
        initial = {'username': target_user.username, 'full_name': core.full_name,
                   'account_status': core.account_status, 'edit_token': edit_token}
        super().__init__(*args, target_user=target_user, initial=initial, **kwargs)
        self.order_fields(['full_name', 'username', 'account_status', 'new_password1', 'new_password2', 'edit_token'])
