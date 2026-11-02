"""Company-admin operator management with tenant-scoped checks and atomic audit."""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.views.decorators.debug import sensitive_variables

from accounts.access import has_tenant_role
from accounts.credential_services import validate_login_username
from accounts.models import UserProfile
from auditlog.services import record_audit_event
from .models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment
from .user_services import create_company_user


class OperatorAuditUnavailable(Exception):
    pass


class OperatorConflict(ValidationError):
    pass


SALT = 'homaniroo.p03.operator-edit.v1'


def _locked_tables():
    """Serialize privilege, identity and membership mutations in PostgreSQL.

    This lock is transaction-scoped. SQLite has no equivalent table-lock syntax;
    it is only used for unit tests, not production deployment.
    """
    if connection.vendor != 'postgresql':
        return
    User = get_user_model()
    models = (User, UserProfile, Group, User.groups.through, User.user_permissions.through,
              Group.permissions.through, Tenant, CoreUser, Role, TenantMembership, UserRoleAssignment)
    names = sorted({model._meta.db_table for model in models})
    quoted = ', '.join(connection.ops.quote_name(name) for name in names)
    with connection.cursor() as cursor:
        cursor.execute('LOCK TABLE ' + quoted + ' IN SHARE ROW EXCLUSIVE MODE')


def _actor(actor, tenant, *, lock=False):
    if not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied
    tenant_id = getattr(tenant, 'tenant_id', None)
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise PermissionDenied
    users = get_user_model().objects
    if lock:
        users = users.select_for_update()
    current = users.filter(pk=actor.pk).first()
    if not current or not has_tenant_role(current, tenant_id, ('COMPANY_ADMIN',)):
        raise PermissionDenied
    if not Tenant.objects.filter(pk=tenant_id, status='ACTIVE').exists():
        raise PermissionDenied
    return current, tenant_id


def _target(actor, tenant_id, user_id, *, lock=False):
    """Return exactly one ordinary operator with no other tenant or privilege."""
    users = get_user_model().objects
    if lock:
        users = users.select_for_update()
    user = users.filter(pk=user_id).first()
    if not user or user.pk == actor.pk or user.is_superuser or user.is_staff:
        raise PermissionDenied
    profiles = UserProfile.objects
    if lock:
        profiles = profiles.select_for_update()
    profile = profiles.filter(user_id=user.pk).first()
    if profile is None or not profile.core_user_id:
        raise PermissionDenied
    cores = CoreUser.objects
    if lock:
        cores = cores.select_for_update()
    core = cores.filter(pk=profile.core_user_id).first()
    if (core is None or core.account_status not in ('ACTIVE', 'INACTIVE')
            or user.is_active != (core.account_status == 'ACTIVE')
            or core.username != user.username):
        raise PermissionDenied
    memberships = TenantMembership.objects.filter(core_user_id=core.pk)
    assignments = UserRoleAssignment.objects.filter(core_user_id=core.pk)
    if lock:
        memberships = memberships.select_for_update()
        assignments = assignments.select_for_update()
    memberships = list(memberships.order_by('pk')[:2])
    assignments = list(assignments.select_related('role').order_by('pk')[:2])
    if (len(memberships) != 1 or memberships[0].tenant_id != tenant_id
            or not memberships[0].is_active or len(assignments) != 1
            or assignments[0].tenant_id != tenant_id
            or assignments[0].role.role_code != 'OPERATOR'):
        raise PermissionDenied
    groups = list(user.groups.order_by('name').values_list('name', flat=True))
    if any(name != 'OPERATOR' for name in groups) or user.user_permissions.exists():
        raise PermissionDenied
    if user.groups.filter(permissions__isnull=False).exists():
        raise PermissionDenied
    return user, profile, core, memberships[0], assignments[0], groups


def _digest(state):
    user, profile, core, membership, assignment, groups = state
    data = {'user_id': user.pk, 'username': user.username, 'password_hash': user.password,
            'first_name': user.first_name, 'email': user.email, 'active': user.is_active,
            'superuser': user.is_superuser, 'staff': user.is_staff,
            'profile_id': profile.pk, 'core_id': profile.core_user_id,
            'must_change_password': profile.must_change_password, 'full_name': core.full_name,
            'core_username': core.username, 'core_email': core.email,
            'core_status': core.account_status, 'membership_id': membership.pk,
            'tenant_id': membership.tenant_id, 'membership_active': membership.is_active,
            'assignment_id': assignment.pk, 'role_id': assignment.role_id, 'groups': groups}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def operator_edit_state(*, actor, tenant, user_id):
    current, tenant_id = _actor(actor, tenant)
    state = _target(current, tenant_id, user_id)
    token = signing.dumps({'user_id': user_id, 'tenant_id': tenant_id, 'digest': _digest(state)},
                          salt=SALT, compress=True)
    return {'user': state[0], 'core': state[2], 'token': token}


def list_operators(*, actor, tenant):
    current, tenant_id = _actor(actor, tenant)
    ids = (TenantMembership.objects.filter(tenant_id=tenant_id, is_active=True,
        core_user__role_assignments__tenant_id=tenant_id,
        core_user__role_assignments__role__role_code='OPERATOR')
        .values_list('core_user_id', flat=True).distinct())
    rows = []
    for profile in UserProfile.objects.filter(core_user_id__in=ids).select_related('user').order_by('user__username'):
        try:
            state = _target(current, tenant_id, profile.user_id)
        except PermissionDenied:
            continue
        rows.append({'user': state[0], 'core': state[2]})
    return rows


def _audit(actor, tenant_id, user_id, operation, changed, request):
    event = record_audit_event(action='CREATE_OR_UPDATE', user=actor, request=request,
        tenant=tenant_id, entity_type='auth_user', entity_id=user_id,
        description='مدیریت اپراتور شرکت', metadata={'operation': operation, 'changed_fields': changed},
        severity='IMPORTANT')
    if event is None:
        raise OperatorAuditUnavailable


@sensitive_variables('password')
@transaction.atomic
def create_operator(*, actor, tenant, username, full_name, email, password, request=None):
    _locked_tables()
    current, tenant_id = _actor(actor, tenant, lock=True)
    username = validate_login_username(username)
    full_name = (full_name or '').strip()
    email = (email or '').strip()
    if not full_name or len(full_name) > 200:
        raise ValidationError('نام کامل معتبر وارد کنید.')
    if email:
        if len(email) > 254:
            raise ValidationError('ایمیل بیش از حد طولانی است.')
        validate_email(email)
    if not password:
        raise ValidationError('رمز عبور الزامی است.')
    candidate = get_user_model()(username=username, first_name=full_name[:150], email=email)
    validate_password(password, user=candidate)
    role = Role.objects.filter(role_code='OPERATOR').first()
    if role is None:
        raise ValidationError('نقش اپراتور موجود نیست؛ حسابی ساخته نشد.')
    group = Group.objects.filter(name='OPERATOR').first()
    if group and group.permissions.exists():
        raise ValidationError('گروه اپراتور مجوزهای اضافی دارد؛ ساخت حساب متوقف شد.')
    real_tenant = Tenant.objects.get(pk=tenant_id, status='ACTIVE')
    user, core = create_company_user(username=username, full_name=full_name, email=email,
                                     password=password, tenant=real_tenant, role=role)
    _target(current, tenant_id, user.pk, lock=True)
    _audit(current, tenant_id, user.pk, 'company_operator_create', ['account', 'membership', 'role'], request)
    return user, core


@sensitive_variables('new_password')
@transaction.atomic
def update_operator(*, actor, tenant, user_id, full_name, username, account_status,
                    new_password, edit_token, request=None):
    _locked_tables()
    current, tenant_id = _actor(actor, tenant, lock=True)
    state = _target(current, tenant_id, user_id, lock=True)
    try:
        token = signing.loads(edit_token, salt=SALT, max_age=3600)
    except (signing.BadSignature, TypeError, ValueError):
        raise OperatorConflict('فرم معتبر نیست یا مهلت آن گذشته؛ تغییری ذخیره نشد.')
    if (not isinstance(token, dict) or token.get('user_id') != user_id or token.get('tenant_id') != tenant_id
            or token.get('digest') != _digest(state)):
        raise OperatorConflict('اطلاعات حساب تغییر کرده است؛ صفحه را دوباره باز کنید.')
    user, profile, core, _, _, _ = state
    if account_status not in ('ACTIVE', 'INACTIVE'):
        raise ValidationError('وضعیت معتبر نیست.')
    full_name = (full_name or '').strip()
    if not full_name or len(full_name) > 200:
        raise ValidationError('نام کامل معتبر وارد کنید.')
    username = validate_login_username(username, user_id=user.pk, core_user_id=core.pk)
    changed, auth_fields, core_fields = [], [], []
    for field, value in (('username', username), ('first_name', full_name[:150]),
                         ('is_active', account_status == 'ACTIVE')):
        if getattr(user, field) != value:
            setattr(user, field, value); auth_fields.append(field); changed.append(field)
    for field, value in (('username', username), ('full_name', full_name), ('account_status', account_status)):
        if getattr(core, field) != value:
            setattr(core, field, value); core_fields.append(field); changed.append('core_' + field)
    if new_password:
        validate_password(new_password, user=user)
        user.set_password(new_password); auth_fields.append('password'); changed.append('password')
    if auth_fields:
        user.save(update_fields=auth_fields)
    if core_fields:
        core.save(update_fields=[*core_fields, 'updated_at'])
    if changed:
        _audit(current, tenant_id, user.pk, 'company_operator_update', changed, request)
    return user, changed
