from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import record_audit_event


@receiver(user_logged_in, dispatch_uid="auditlog_login_success")
def audit_login_success(sender, request, user, **kwargs):
    record_audit_event(action="LOGIN_SUCCESS", request=request, user=user, description="ورود موفق به سامانه")


@receiver(user_logged_out, dispatch_uid="auditlog_logout")
def audit_logout(sender, request, user, **kwargs):
    record_audit_event(action="LOGOUT", request=request, user=user, description="خروج از سامانه")


@receiver(user_login_failed, dispatch_uid="auditlog_login_failed")
def audit_login_failed(sender, credentials, request, **kwargs):
    username = (credentials or {}).get("username", "")
    record_audit_event(
        action="LOGIN_FAILED", request=request, description=f"ورود ناموفق برای نام کاربری {username}",
        metadata={"username": username}, success=False, severity="IMPORTANT",
    )
