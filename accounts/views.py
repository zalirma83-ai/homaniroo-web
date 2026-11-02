from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods

from auditlog.services import record_audit_event
from .access import superadmin_required
from .credential_services import CredentialAuditUnavailable, set_user_credentials
from .forms import CredentialManagementForm
from .session_utils import get_user_sessions


@login_required
def session_list(request):
    current_key = request.session.session_key
    sessions = [
        {
            "reference": f"{session.session_key[:8]}…",
            "expire_date": session.expire_date,
            "is_current": session.session_key == current_key,
        }
        for session in get_user_sessions(request.user)
    ]
    sessions.sort(key=lambda item: (not item["is_current"], item["expire_date"]))
    return render(request, "accounts/session_list.html", {"sessions": sessions})


@login_required
@require_POST
def logout_other_sessions(request):
    current_key = request.session.session_key
    removed = 0
    for session in list(get_user_sessions(request.user)):
        if session.session_key != current_key:
            session.delete()
            removed += 1
    messages.success(request, f"{removed} نشست دیگر بسته شد.")
    return redirect("accounts:sessions")


@login_required
@require_POST
def logout_all_sessions(request):
    sessions = list(get_user_sessions(request.user))
    for session in sessions:
        session.delete()
    logout(request)
    messages.success(request, "تمام نشست‌های شما بسته شد.")
    return redirect("accounts:login")


# A separate credential path intentionally avoids tenant role/membership updates.
@superadmin_required
@never_cache
@require_http_methods(["GET"])
def credential_user_list(request):
    users = get_user_model().objects.all().order_by("username", "pk")
    return render(request, "accounts/credential_user_list.html", {"credential_users": users})


def _manage_credentials(request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id)
    form = CredentialManagementForm(
        request.POST if request.method == "POST" else None, target_user=target,
    )
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                changed_user, changed_fields = set_user_credentials(
                    actor=request.user, target_id=target.pk,
                    username=form.cleaned_data["username"],
                    new_password=form.cleaned_data.get("new_password1", ""), request=request,
                )
            except ValidationError as error:
                form.add_error(None, error)
            except IntegrityError:
                form.add_error("username", "این نام کاربری قابل ثبت نیست؛ اطلاعات را دوباره بررسی کنید.")
            except CredentialAuditUnavailable:
                form.add_error(None, "ثبت ممیزی ممکن نشد؛ هیچ تغییری در اطلاعات ورود ذخیره نشد.")
                status = 503
            else:
                if changed_user.pk == request.user.pk and "password" in changed_fields:
                    update_session_auth_hash(request, changed_user)
                messages.success(request, "اطلاعات ورود ذخیره شد." if changed_fields else "اطلاعات ورود تغییری نداشت.")
                return redirect("accounts:user_credentials", user_id=target.pk)
        # Record failure by operation/target only, never posted values or errors.
        record_audit_event(
            action="CREATE_OR_UPDATE", request=request, entity_type="auth_user", entity_id=target.pk,
            description="درخواست ناموفق ویرایش اطلاعات ورود",
            metadata={"operation": "credentials_update"}, success=False, severity="IMPORTANT",
        )
    return render(request, "accounts/credential_form.html", {"form": form, "target_user": target}, status=status)


@sensitive_post_parameters()
@superadmin_required
@never_cache
@require_http_methods(["GET", "POST"])
def manage_own_credentials(request):
    return _manage_credentials(request, request.user.pk)


@sensitive_post_parameters()
@superadmin_required
@never_cache
@require_http_methods(["GET", "POST"])
def manage_credentials(request, user_id):
    return _manage_credentials(request, user_id)
