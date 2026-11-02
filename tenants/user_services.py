from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
import hashlib
import json

from auditlog.services import record_audit_event

from accounts.models import UserProfile

from .models import CoreUser, Role, TenantMembership, UserRoleAssignment


@transaction.atomic
def create_company_user(*, username, full_name, email, password, tenant, role):
    User = get_user_model()
    auth_user = User.objects.create_user(
        username=username,
        email=email or "",
        password=password,
    )
    auth_user.first_name = full_name[:150]
    auth_user.is_active = True
    auth_user.save(update_fields=["first_name", "is_active"])

    core_user = CoreUser.objects.create(
        full_name=full_name,
        email=email or None,
        username=username,
        account_status="ACTIVE",
    )

    profile, _ = UserProfile.objects.get_or_create(user=auth_user)
    profile.core_user_id = core_user.user_id
    profile.must_change_password = False
    profile.save(update_fields=["core_user_id", "must_change_password", "updated_at"])
    auth_user._state.fields_cache["profile"] = profile

    TenantMembership.objects.create(
        tenant=tenant,
        core_user=core_user,
        is_active=True,
    )
    UserRoleAssignment.objects.create(
        core_user=core_user,
        role=role,
        tenant=tenant,
    )

    group, _ = Group.objects.get_or_create(name=role.role_code)
    auth_user.groups.set([group])
    return auth_user, core_user


class CompanyUserEditConflict(ValidationError):
    """The account, membership or selected company's role changed after GET."""


class CompanyUserAuditUnavailable(Exception):
    """No edit may commit without its explicit access-change audit event."""


EDIT_TOKEN_SALT = "homaniroo.company-user-edit.a04"
EDIT_TOKEN_MAX_AGE = 3600


def _require_actor(actor):
    if not (getattr(actor, "is_authenticated", False) and
            getattr(actor, "is_active", False) and getattr(actor, "is_superuser", False)):
        raise PermissionDenied


def _scope_id(tenant):
    value = getattr(tenant, "tenant_id", tenant)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("شرکت موردنظر برای ویرایش معتبر نیست؛ تغییری انجام نشد.")
    return value


def _scope_rows(core_user_id, tenant_id, *, lock=False):
    members = TenantMembership.objects.filter(core_user_id=core_user_id, tenant_id=tenant_id)
    roles = UserRoleAssignment.objects.filter(core_user_id=core_user_id, tenant_id=tenant_id)
    if lock:
        members = members.select_for_update()
        roles = roles.select_for_update()
    members = list(members.order_by("tenant_user_id")[:2])
    roles = list(roles.order_by("user_role_assignment_id")[:2])
    if len(members) != 1:
        raise ValidationError("عضویت یکتای کاربر در شرکت انتخاب‌شده پیدا نشد؛ عضویت جدیدی ساخته نمی‌شود.")
    if len(roles) > 1:
        raise ValidationError("این کاربر در شرکت انتخاب‌شده چند نقش دارد؛ برای جلوگیری از حذف یا انتخاب خودکار نقش، ویرایش متوقف شد. ابتدا نقش‌ها را تعیین تکلیف کنید.")
    return members[0], roles[0] if roles else None


def _state_digest(core_user, auth_user, profile, membership, assignment):
    state = {
        "core_user_id": core_user.pk, "core_updated_at": core_user.updated_at.isoformat(),
        "full_name": core_user.full_name, "core_email": core_user.email,
        "account_status": core_user.account_status,
        "profile_id": profile.pk, "auth_user_id": auth_user.pk,
        "auth_first_name": auth_user.first_name, "auth_email": auth_user.email,
        "auth_active": auth_user.is_active, "auth_superuser": auth_user.is_superuser,
        "membership_id": membership.pk, "tenant_id": membership.tenant_id,
        "membership_active": membership.is_active, "joined_at": membership.joined_at.isoformat(),
        "role_assignment_id": assignment.pk if assignment else None,
        "role_id": assignment.role_id if assignment else None,
        "assigned_at": assignment.assigned_at.isoformat() if assignment else None,
    }
    raw = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def company_user_edit_state(*, actor, core_user, tenant):
    """Read-only form state. Signed token carries a digest, not names or secrets."""
    _require_actor(actor)
    tenant_id = _scope_id(tenant)
    core_user = CoreUser.objects.get(pk=core_user.pk)
    profile = UserProfile.objects.select_related("user").filter(core_user_id=core_user.pk).first()
    if profile is None:
        raise ValidationError("ارتباط این کاربر با حساب ورود معتبر نیست؛ ویرایش و ایجاد ارتباط خودکار انجام نمی‌شود.")
    membership, assignment = _scope_rows(core_user.pk, tenant_id)
    token = signing.dumps({"core_user_id": core_user.pk, "tenant_id": tenant_id,
        "digest": _state_digest(core_user, profile.user, profile, membership, assignment)},
        salt=EDIT_TOKEN_SALT, compress=True)
    return {"core_user": core_user, "auth_user": profile.user,
            "membership": membership, "assignment": assignment, "edit_token": token}


@transaction.atomic
def update_company_user(*, core_user, full_name, email, tenant, role, account_status,
                        actor, edit_token, request=None):
    """Change one company's role, never replace memberships or global groups."""
    _require_actor(actor)
    tenant_id = _scope_id(tenant)
    if account_status not in {"ACTIVE", "INACTIVE", "LOCKED"}:
        raise ValidationError("وضعیت حساب معتبر نیست.")
    full_name = (full_name or "").strip()
    email = (email or "").strip()
    if not full_name or len(full_name) > 200:
        raise ValidationError("نام کامل معتبر وارد کنید.")
    try:
        token = signing.loads(edit_token, salt=EDIT_TOKEN_SALT, max_age=EDIT_TOKEN_MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError):
        raise CompanyUserEditConflict("فرم معتبر نیست یا مهلت آن گذشته است؛ هیچ تغییری ذخیره نشد. صفحه را دوباره باز کنید.", code="stale_user_edit")
    if not isinstance(token, dict) or token.get("core_user_id") != core_user.pk or token.get("tenant_id") != tenant_id:
        raise CompanyUserEditConflict("شرکت یا کاربر فرم با درخواست فعلی یکسان نیست؛ تغییری انجام نشد.", code="stale_user_edit")
    # Match the credential service's lock order: auth user -> profile -> core.
    mapping = UserProfile.objects.filter(core_user_id=core_user.pk).values("pk", "user_id").first()
    if mapping is None:
        raise ValidationError("ارتباط حساب ورود موجود نیست؛ ارتباط یا حساب جدید ساخته نمی‌شود.")
    auth_user = get_user_model().objects.select_for_update().get(pk=mapping["user_id"])
    profile = UserProfile.objects.select_for_update().get(pk=mapping["pk"])
    if profile.core_user_id != core_user.pk or profile.user_id != auth_user.pk:
        raise CompanyUserEditConflict("ارتباط هویت کاربر تغییر کرده است؛ صفحه را دوباره باز کنید.", code="stale_user_edit")
    core_user = CoreUser.objects.select_for_update().get(pk=core_user.pk)
    membership, assignment = _scope_rows(core_user.pk, tenant_id, lock=True)
    if token.get("digest") != _state_digest(core_user, auth_user, profile, membership, assignment):
        raise CompanyUserEditConflict("اطلاعات حساب، عضویت یا نقش از زمان بازکردن فرم تغییر کرده است؛ تغییری ذخیره نشد. مقادیر واردشده را نگه دارید و صفحه را دوباره باز کنید.", code="stale_user_edit")
    selected_role = Role.objects.filter(pk=getattr(role, "pk", None)).exclude(role_code="SUPERADMIN").first()
    if selected_role is None:
        raise ValidationError("نقش انتخاب‌شده برای این فرم مجاز نیست.")
    if auth_user.is_superuser and account_status != "ACTIVE":
        raise ValidationError("غیرفعال یا قفل‌کردن حساب سوپرادمین از فرم کاربر شرکت مجاز نیست.")
    core_fields = []
    auth_fields = []
    for field, value in (("full_name", full_name), ("email", email or None), ("account_status", account_status)):
        if getattr(core_user, field) != value:
            setattr(core_user, field, value); core_fields.append(field)
    for field, value in (("first_name", full_name[:150]), ("email", email), ("is_active", account_status == "ACTIVE")):
        if getattr(auth_user, field) != value:
            setattr(auth_user, field, value); auth_fields.append(field)
    old_role_id = assignment.role_id if assignment else None
    role_changed = old_role_id != selected_role.pk
    changed = ["core." + field for field in core_fields] + ["auth." + field for field in auth_fields]
    if role_changed:
        changed.append("company_role")
    if not changed:
        return auth_user, []
    if core_fields:
        core_user.save(update_fields=[*core_fields, "updated_at"])
    if auth_fields:
        auth_user.save(update_fields=auth_fields)
    if role_changed:
        if assignment is None:
            assignment = UserRoleAssignment.objects.create(core_user=core_user, tenant_id=tenant_id, role=selected_role)
        else:
            assignment.role = selected_role
            assignment.assigned_at = timezone.now()
            assignment.save(update_fields=["role", "assigned_at"])
    event = record_audit_event(action="CREATE_OR_UPDATE", user=actor, request=request,
        tenant=tenant_id, entity_type="auth_user", entity_id=auth_user.pk,
        description="ویرایش مشخصات حساب و نقش شرکت توسط سوپرادمین",
        metadata={"operation": "company_user_update", "core_user_id": core_user.pk,
                  "tenant_id": tenant_id, "membership_id": membership.pk,
                  "role_assignment_id": assignment.pk if assignment else None,
                  "old_role_id": old_role_id, "new_role_id": selected_role.pk,
                  "changed_fields": changed}, severity="IMPORTANT")
    if event is None:
        raise CompanyUserAuditUnavailable
    return auth_user, changed
