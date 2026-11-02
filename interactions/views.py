from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render

from accounts.access import role_required
from common.tenant_access import tenant_required
from .forms import (
    FollowUpActionForm,
    FollowUpRescheduleForm,
    InteractionCreateForm,
)
from .services import (
    build_work_queue,
    close_follow_up,
    get_customer,
    get_open_follow_up,
    record_interaction,
    reschedule_follow_up,
)


OPERATIONAL_ROLES = ("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def work_queue(request):
    context = build_work_queue(
        request.tenant,
        scope=request.GET.get("scope", "all"),
        query=request.GET.get("q", ""),
        customer_status=request.GET.get("customer_status", ""),
        ordering=request.GET.get("order", "due"),
        page_number=request.GET.get("followup_page"),
        history_page_number=request.GET.get("history_page"),
        ownership=request.GET.get("ownership", ""),
    )
    return render(request, "interactions/work_queue.html", context)


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def interaction_create(request, customer_id=None):
    fixed_customer = None
    if customer_id is not None:
        fixed_customer = get_customer(request.tenant, customer_id)
    initial = {}
    if customer_id is not None:
        initial["tenant_customer_id"] = str(customer_id)
    requested_result = request.GET.get("result")
    if requested_result:
        initial["interaction_status"] = requested_result
    form = InteractionCreateForm(
        request.POST or None,
        tenant=request.tenant,
        fixed_customer_id=customer_id,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        record_interaction(
            tenant=request.tenant,
            user=request.user,
            cleaned_data=form.cleaned_data,
        )
        messages.success(request, "تماس و برنامه پیگیری با موفقیت ثبت شد.")
        return redirect("interactions:work_queue")
    return render(
        request,
        "interactions/interaction_form.html",
        {"form": form, "fixed_customer": fixed_customer},
    )


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def follow_up_action(request, follow_up_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = FollowUpActionForm(request.POST)
    if form.is_valid():
        try:
            close_follow_up(
                tenant=request.tenant,
                follow_up_id=follow_up_id,
                user=request.user,
                action=form.cleaned_data["action"],
                note=form.cleaned_data.get("note", ""),
            )
        except ValidationError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, "وضعیت پیگیری به‌روزرسانی شد.")
    else:
        messages.error(request, "عملیات انتخاب‌شده معتبر نیست.")
    return redirect("interactions:work_queue")


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def follow_up_reschedule(request, follow_up_id):
    follow_up = get_open_follow_up(request.tenant, follow_up_id)
    customer = get_customer(request.tenant, follow_up.tenant_customer_id)
    form = FollowUpRescheduleForm(
        request.POST or None,
        initial={"reason": follow_up.reason or ""},
    )
    if request.method == "POST" and form.is_valid():
        reschedule_follow_up(
            tenant=request.tenant,
            follow_up_id=follow_up_id,
            user=request.user,
            due_at=form.cleaned_data["due_jalali"],
            reason=form.cleaned_data.get("reason", ""),
        )
        messages.success(request, "زمان پیگیری با موفقیت تغییر کرد.")
        return redirect("interactions:work_queue")
    return render(
        request,
        "interactions/follow_up_reschedule.html",
        {"form": form, "follow_up": follow_up, "customer": customer},
    )


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def alert_center(request):
    from .alert_services import build_alert_center

    context = build_alert_center(
        request.tenant,
        status=request.GET.get("status", "active"),
        alert_group=request.GET.get("type", ""),
        severity=request.GET.get("severity", ""),
        query=request.GET.get("q", ""),
        ownership=request.GET.get("ownership", ""),
    )
    context["page"] = Paginator(context.pop("rows"), 25).get_page(
        request.GET.get("page")
    )
    return render(request, "interactions/alert_center.html", context)


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def alert_sync(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .alert_services import synchronize_alerts

    result = synchronize_alerts(request.tenant)
    messages.success(
        request,
        "هشدارها به‌روزرسانی شدند: "
        f"{result['created']} مورد جدید، {result['updated']} مورد اصلاح‌شده و "
        f"{result['resolved']} مورد بسته‌شده.",
    )
    return redirect("interactions:alert_center")


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def alert_action(request, alert_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .alert_services import update_alert_status
    from .forms import AlertStatusForm

    form = AlertStatusForm(request.POST)
    if form.is_valid():
        update_alert_status(
            tenant=request.tenant,
            alert_id=alert_id,
            status=form.cleaned_data["status"],
        )
        messages.success(request, "وضعیت هشدار به‌روزرسانی شد.")
    else:
        messages.error(request, "وضعیت انتخاب‌شده معتبر نیست.")
    return redirect("interactions:alert_center")
