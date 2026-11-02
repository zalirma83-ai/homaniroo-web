"""Superadmin-only credential changes; no role or membership writes."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import RegexValidator
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from auditlog.services import record_audit_event
from tenants.models import CoreUser
from .models import UserProfile


class CredentialAuditUnavailable(Exception):
    """A credential change must roll back if its audit cannot be stored."""


def validate_login_username(username, *, user_id=None, core_user_id=None):
    username = (username or "").strip().lower()
    RegexValidator(r"\A[A-Za-z0-9_.-]{1,150}\Z", "نام کاربری معتبر وارد کنید.")(username)
    if get_user_model().objects.filter(username__iexact=username).exclude(pk=user_id).exists():
        raise ValidationError("این نام کاربری قبلاً ثبت شده است.")
    if CoreUser.objects.filter(username__iexact=username).exclude(user_id=core_user_id).exists():
        raise ValidationError("این نام کاربری در اطلاعات تجاری موجود است.")
    return username


@sensitive_variables("new_password")
@transaction.atomic
def set_user_credentials(*, actor, target_id, username, new_password="", request=None):
    if not (actor.is_authenticated and actor.is_active and actor.is_superuser):
        raise PermissionDenied
    user = get_user_model().objects.select_for_update().get(pk=target_id)
    profile = UserProfile.objects.select_for_update().filter(user_id=user.pk).first()
    core_user = None
    if profile and profile.core_user_id:
        core_user = CoreUser.objects.select_for_update().filter(user_id=profile.core_user_id).first()
        if core_user is None:
            raise ValidationError("ارتباط حساب با کاربر تجاری معتبر نیست؛ تغییری انجام نشد.")
    username = validate_login_username(
        username, user_id=user.pk, core_user_id=profile.core_user_id if profile else None,
    )
    old_username = user.username
    user.username = username
    changed = []
    auth_fields = []
    if username != old_username:
        changed.append("username")
        auth_fields.append("username")
    if new_password:
        validate_password(new_password, user=user)
        user.set_password(new_password)
        auth_fields.append("password")
        changed.append("password")
    if auth_fields:
        user.save(update_fields=auth_fields)
    if core_user is not None and core_user.username != username:
        core_user.username = username
        core_user.save(update_fields=["username", "updated_at"])
        changed.append("core_username")
    if profile and profile.must_change_password:
        profile.must_change_password = False
        profile.save(update_fields=["must_change_password", "updated_at"])
        changed.append("must_change_password")
    if changed:
        event = record_audit_event(
            action="CREATE_OR_UPDATE", request=request, user=actor,
            entity_type="auth_user", entity_id=user.pk,
            description="ویرایش اطلاعات ورود توسط سوپرادمین",
            metadata={"operation": "credentials_update", "changed_fields": changed,
                      "old_username": old_username, "new_username": username},
            severity="IMPORTANT",
        )
        if event is None:
            raise CredentialAuditUnavailable
    return user, changed
