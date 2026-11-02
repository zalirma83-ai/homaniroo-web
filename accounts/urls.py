from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import PersianAuthenticationForm
from .access import superadmin_required

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=PersianAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        views.manage_own_credentials,
        name="password_change",
    ),
    path(
        "password/change/done/",
        superadmin_required(auth_views.PasswordChangeDoneView.as_view(
            template_name="accounts/password_change_done.html"
        )),
        name="password_change_done",
    ),
    path("users/", views.credential_user_list, name="credential_user_list"),
    path("users/<int:user_id>/credentials/", views.manage_credentials, name="user_credentials"),
    path("sessions/", views.session_list, name="sessions"),
    path(
        "sessions/logout-others/",
        views.logout_other_sessions,
        name="logout_other_sessions",
    ),
    path(
        "sessions/logout-all/",
        views.logout_all_sessions,
        name="logout_all_sessions",
    ),
]
