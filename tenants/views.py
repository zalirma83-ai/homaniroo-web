from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseNotAllowed
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters

from accounts.access import role_required, superadmin_required
from accounts.credential_services import CredentialAuditUnavailable
from auditlog.services import record_audit_event
from common.tenant_access import tenant_required
from contracts.models import Contract
from customers.models import TenantCustomer
from accounts.models import UserProfile

from .user_forms import CompanyUserCreateForm, CompanyUserUpdateForm
from .user_services import (create_company_user, update_company_user, company_user_edit_state,
                            CompanyUserEditConflict, CompanyUserAuditUnavailable)

from .forms import TenantForm, TenantSelectionForm
from .models import (
    CoreUser, Tenant, TenantMembership, UserRoleAssignment,
)
from .services import ACTIVE_TENANT_SESSION_KEY


@login_required
def select_tenant(request):
    form = TenantSelectionForm(request.POST or None, user=request.user)
    next_url = request.POST.get("next") or request.GET.get("next")

    if request.method == "POST" and form.is_valid():
        tenant = form.cleaned_data["tenant"]
        request.session[ACTIVE_TENANT_SESSION_KEY] = tenant.tenant_id
        messages.success(request, f"شرکت فعال به «{tenant.display_name}» تغییر کرد.")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("dashboard:home")

    return render(
        request,
        "tenants/select_tenant.html",
        {"form": form, "next": next_url or ""},
    )


@login_required
@require_POST
def clear_tenant(request):
    request.session.pop(ACTIVE_TENANT_SESSION_KEY, None)
    messages.success(request, "حالت مشاهده همه شرکت‌ها فعال شد.")
    return redirect("dashboard:home")


@role_required("SUPERADMIN")
def tenant_list(request):
    tenants = Tenant.objects.all().order_by("tenant_id")
    return render(request, "tenants/tenant_list.html", {"tenants": tenants})


@role_required("SUPERADMIN")
@transaction.atomic
def tenant_create(request):
    form = TenantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tenant = form.save()
        messages.success(request, f"شرکت «{tenant.display_name}» ایجاد شد.")
        return redirect("tenants:list")
    return render(
        request,
        "tenants/tenant_form.html",
        {"form": form, "page_title": "ایجاد شرکت"},
    )


@role_required("SUPERADMIN")
@transaction.atomic
def tenant_update(request, tenant_id):
    tenant = get_object_or_404(Tenant, tenant_id=tenant_id)
    form = TenantForm(request.POST or None, instance=tenant)
    if request.method == "POST" and form.is_valid():
        tenant = form.save()
        messages.success(request, f"شرکت «{tenant.display_name}» به‌روزرسانی شد.")
        return redirect("tenants:list")
    return render(
        request,
        "tenants/tenant_form.html",
        {"form": form, "page_title": "ویرایش شرکت", "tenant": tenant},
    )


@role_required("SUPERADMIN")
def tenant_delete(request, tenant_id):
    tenant = get_object_or_404(Tenant, tenant_id=tenant_id)

    if request.method == "POST":
        selected_id = request.session.get(ACTIVE_TENANT_SESSION_KEY)
        try:
            with transaction.atomic():
                tenant.delete()
        except IntegrityError:
            messages.error(
                request,
                "این شرکت دارای اطلاعات وابسته است و قابل حذف نیست؛ "
                "در صورت نیاز آن را غیرفعال کنید.",
            )
            return redirect("tenants:update", tenant_id=tenant_id)

        if selected_id == tenant_id:
            request.session.pop(ACTIVE_TENANT_SESSION_KEY, None)
        messages.success(request, "شرکت بدون داده وابسته با موفقیت حذف شد.")
        return redirect("tenants:list")

    return render(
        request,
        "tenants/tenant_confirm_delete.html",
        {"tenant": tenant},
    )


@superadmin_required
def company_user_list(request):
    memberships = list(
        TenantMembership.objects.select_related("tenant", "core_user").order_by(
            "tenant__display_name", "core_user__full_name"
        )
    )
    assignments = {
        (item.core_user_id, item.tenant_id): item
        for item in UserRoleAssignment.objects.select_related("role", "tenant")
        .filter(tenant__isnull=False)
    }
    profile_map = {
        item.core_user_id: item
        for item in UserProfile.objects.select_related("user").filter(
            core_user_id__in=[membership.core_user_id for membership in memberships]
        )
    }

    rows = []
    for membership in memberships:
        assignment = assignments.get(
            (membership.core_user_id, membership.tenant_id)
        )
        profile = profile_map.get(membership.core_user_id)
        rows.append(
            {
                "membership": membership,
                "role": assignment.role if assignment else None,
                "auth_user": profile.user if profile else None,
            }
        )
    return render(request, "tenants/company_user_list.html", {"rows": rows})


@sensitive_post_parameters()
@superadmin_required
def company_user_create(request):
    form = CompanyUserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                auth_user, core_user = create_company_user(
                    username=form.cleaned_data["username"],
                    full_name=form.cleaned_data["full_name"],
                    email=form.cleaned_data["email"],
                    password=form.cleaned_data["password1"],
                    tenant=form.cleaned_data["tenant"],
                    role=form.cleaned_data["role"],
                )
                event = record_audit_event(
                    action="CREATE_OR_UPDATE", request=request, tenant=form.cleaned_data["tenant"],
                    entity_type="auth_user", entity_id=auth_user.pk,
                    description="ایجاد حساب ورود توسط سوپرادمین",
                    metadata={"operation": "account_create", "core_user_id": core_user.user_id},
                    severity="IMPORTANT",
                )
                if event is None:
                    raise CredentialAuditUnavailable
        except (IntegrityError, CredentialAuditUnavailable):
            form.add_error(None, "ساخت حساب ذخیره نشد؛ نام کاربری یا امکان ثبت ممیزی را بررسی کنید.")
            return render(request, "tenants/company_user_form.html",
                          {"form": form, "page_title": "ایجاد کاربر شرکت"}, status=400)
        messages.success(
            request,
            f"کاربر «{core_user.full_name}» با نام کاربری {auth_user.username} ایجاد شد.",
        )
        return redirect("tenants:user_list")
    return render(
        request,
        "tenants/company_user_form.html",
        {"form": form, "page_title": "ایجاد کاربر شرکت"},
    )


@superadmin_required
def company_user_update(request, core_user_id):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    core_user = get_object_or_404(CoreUser, user_id=core_user_id)
    memberships = list(TenantMembership.objects.filter(core_user=core_user)
                       .select_related("tenant").order_by("tenant__display_name", "tenant_user_id"))
    raw_tenant = request.GET.get("tenant", "")
    if raw_tenant:
        try:
            selected_id = int(raw_tenant)
            if selected_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise Http404("شرکت انتخاب‌شده معتبر نیست.")
        selected = [member for member in memberships if member.tenant_id == selected_id]
        if len(selected) != 1:
            raise Http404("عضویت یکتای کاربر در این شرکت پیدا نشد.")
        membership = selected[0]
    elif len(memberships) == 1:
        membership = memberships[0]
    else:
        return render(request, "tenants/company_user_scope.html", {
            "core_user": core_user, "memberships": memberships,
            "scope_error": "برای جلوگیری از انتخاب خودکار، ابتدا شرکت موردنظر را انتخاب کنید." if memberships else
                           "این کاربر عضویت شرکتی ندارد؛ از این فرم عضویت جدید ساخته نمی‌شود.",
        }, status=400 if request.method == "POST" else 200)
    tenant = membership.tenant
    try:
        state = company_user_edit_state(actor=request.user, core_user=core_user, tenant=tenant)
    except ValidationError as error:
        return render(request, "tenants/company_user_scope.html", {
            "core_user": core_user, "memberships": [], "scope_error": " ".join(error.messages),
        }, status=400)
    core_user = state["core_user"]
    assignment = state["assignment"]
    initial = {"full_name": core_user.full_name, "email": core_user.email,
               "tenant": tenant.pk, "role": assignment.role_id if assignment else None,
               "account_status": core_user.account_status, "edit_token": state["edit_token"]}
    form = CompanyUserUpdateForm(request.POST if request.method == "POST" else None,
                                 initial=initial, tenant=tenant)
    context = {"form": form, "page_title": "ویرایش کاربر شرکت", "core_user": core_user,
               "scope_edit": True, "edit_tenant": tenant, "membership_active": state["membership"].is_active}
    if request.method == "POST":
        if not form.is_valid():
            return render(request, "tenants/company_user_form.html", context, status=400)
        try:
            _, changed = update_company_user(core_user=core_user,
                full_name=form.cleaned_data["full_name"], email=form.cleaned_data["email"],
                tenant=tenant, role=form.cleaned_data["role"], account_status=form.cleaned_data["account_status"],
                actor=request.user, edit_token=form.cleaned_data["edit_token"], request=request)
        except CompanyUserEditConflict as error:
            form.add_error(None, error); context["edit_blocked"] = True
            return render(request, "tenants/company_user_form.html", context, status=409)
        except ValidationError as error:
            form.add_error(None, error)
            return render(request, "tenants/company_user_form.html", context, status=400)
        except (IntegrityError, CompanyUserAuditUnavailable):
            form.add_error(None, "ویرایش ذخیره نشد؛ یکپارچگی اطلاعات یا ثبت ممیزی ممکن نبود. بدون بررسی دوباره تلاش نکنید.")
            context["edit_blocked"] = True
            return render(request, "tenants/company_user_form.html", context, status=400)
        if changed:
            messages.success(request, "مشخصات حساب و نقش شرکت انتخاب‌شده ذخیره شد؛ عضویت‌ها و نقش‌های دیگر حفظ شدند.")
        else:
            messages.info(request, "تغییری نسبت به اطلاعات موجود وارد نشده بود.")
        return redirect("tenants:user_list")
    return render(request, "tenants/company_user_form.html", context)


@tenant_required
def tenant_data_preview(request):
    contracts = Contract.objects.for_tenant(request.tenant)
    context = {
        "customer_count": TenantCustomer.objects.for_tenant(request.tenant).count(),
        "contract_count": contracts.count(),
        "sale_count": contracts.filter(contract_direction="SALE").count(),
        "purchase_count": contracts.filter(contract_direction="PURCHASE").count(),
    }
    return render(request, "tenants/tenant_data_preview.html", context)
