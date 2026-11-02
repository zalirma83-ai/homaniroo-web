from .p02_workbook import workbook_rows_for_customer
from .source_notes import source_notes_for_customer
from .provisional_rank import attach_provisional_ranks
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.http import HttpResponseBadRequest
from django.views.decorators.http import require_GET

from accounts.access import role_required
from common.tenant_access import tenant_required
from interactions.models import FollowUp
from .forms import (
    CustomerContactForm,
    DormantRestoreForm,
    DormantTransitionForm,
    InteractionForm,
    ProspectiveCustomerCreateForm,
    ProspectiveCustomerIdentityForm,
)
from .models import CustomerContact, CustomerStatusHistory
from .services import (
    active_contract_rows,
    active_customers_for_tenant,
    add_customer_contact,
    build_customer_profile,
    customer_status_history_rows,
    dormant_customers_for_tenant,
    filter_active_customers,
    filter_dormant_customers,
    get_dormant_customer,
    get_status_change_candidate,
    get_active_customer,
    get_customer_for_profile,
    convert_to_active_customer,
    create_prospective_customer,
    filter_prospective_customers,
    get_prospective_customer,
    interaction_rows_for_customer,
    mark_customer_dormant,
    prospective_customers_for_tenant,
    record_customer_interaction,
    restore_dormant_customer,
    update_prospective_customer,
)
from common.information_status import (
    assess_customer, attach_customer_information, filter_information,
)
from contracts.models import Contract
from .business_services import CYCLES, attach_business_classification, classify_customers
from .ownership import normalize as normalize_ownership


def _detail_context(request, customer, *, contact_form=None, interaction_form=None):
    contacts = list(CustomerContact.objects.for_tenant(request.tenant).filter(tenant_customer=customer))
    attach_business_classification(customer, request.tenant)
    customer.information = assess_customer(customer, contacts)
    follow_ups = FollowUp.objects.for_tenant(request.tenant).filter(
        tenant_customer_id=customer.tenant_customer_id,
        follow_up_status="OPEN",
    ).order_by("due_at", "follow_up_id")
    return {
        "customer": customer,
        "contacts": contacts,
        "interaction_rows": interaction_rows_for_customer(request.tenant, customer),
        "follow_ups": follow_ups,
        "contact_form": contact_form or CustomerContactForm(),
        "interaction_form": interaction_form or InteractionForm(),
    }


@tenant_required
def prospective_list(request):
    base = prospective_customers_for_tenant(request.tenant)
    stats = {
        "total": base.count(),
        "without_contact": base.filter(has_contact=False).count(),
        "without_interaction": base.filter(has_interaction=False).count(),
        "review_required": filter_information(base, "ATTENTION").count(),
    }
    filters = {
        "query": request.GET.get("q", ""),
        "contact_state": request.GET.get("contact", ""),
        "quality": "",  # E04: retired UI filter; ignore old bookmarked query values.
        "ordering": request.GET.get("order", "newest"),
    }
    queryset = filter_prospective_customers(base, **filters)
    filters["cycle"] = request.GET.get("cycle", "")
    if filters["cycle"] not in CYCLES:
        filters["cycle"] = ""
    queryset = classify_customers(queryset, request.tenant, filters["cycle"])
    filters["ownership"] = normalize_ownership(request.GET.get("ownership", ""))
    if filters["ownership"]:
        queryset = queryset.filter(ownership_code=filters["ownership"])
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page.object_list = attach_customer_information(list(page.object_list))
    return render(
        request,
        "customers/prospective_list.html",
        {"page": page, "stats": stats, "filters": filters},
    )


@tenant_required
@role_required("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")
def prospective_create(request):
    form = ProspectiveCustomerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            customer = create_prospective_customer(
                tenant=request.tenant, cleaned_data=form.cleaned_data
            )
        except ValidationError as error:
            form.add_error("official_name", error.message)
        else:
            messages.success(request, "مشترک بالقوه با موفقیت ثبت شد.")
            return redirect("customers:prospective_detail", customer_id=customer.tenant_customer_id)
    return render(request, "customers/prospective_form.html", {"form": form, "page_title": "ثبت مشترک بالقوه"})


@tenant_required
def prospective_detail(request, customer_id):
    customer = get_prospective_customer(request.tenant, customer_id)
    return render(
        request, "customers/prospective_detail.html",
        _detail_context(request, customer),
    )


@tenant_required
@role_required("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")
def prospective_edit(request, customer_id):
    customer = get_prospective_customer(request.tenant, customer_id)
    unit = customer.industrial_unit
    initial = {
        "official_name": unit.official_name,
        "national_id": unit.national_id,
        "economic_code": unit.economic_code,
        "postal_code": unit.postal_code,
        "address": unit.address,
    }
    form = ProspectiveCustomerIdentityForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            update_prospective_customer(
                tenant=request.tenant, customer=customer, cleaned_data=form.cleaned_data
            )
        except ValidationError as error:
            form.add_error("official_name", error.message)
        else:
            messages.success(request, "اطلاعات مشترک بالقوه به‌روزرسانی شد.")
            return redirect("customers:prospective_detail", customer_id=customer_id)
    return render(
        request, "customers/prospective_form.html",
        {"form": form, "page_title": "ویرایش مشترک بالقوه", "customer": customer},
    )


@tenant_required
@role_required("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")
def contact_add(request, customer_id):
    customer = get_prospective_customer(request.tenant, customer_id)
    if request.method != "POST":
        return redirect("customers:prospective_detail", customer_id=customer_id)
    form = CustomerContactForm(request.POST)
    if form.is_valid():
        add_customer_contact(tenant=request.tenant, customer=customer, cleaned_data=form.cleaned_data)
        messages.success(request, "اطلاعات تماس جدید ثبت شد.")
        return redirect("customers:prospective_detail", customer_id=customer_id)
    messages.error(request, "اطلاعات تماس کامل یا معتبر نیست.")
    return render(
        request, "customers/prospective_detail.html",
        _detail_context(request, customer, contact_form=form),
        status=400,
    )


@tenant_required
@role_required("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")
def interaction_add(request, customer_id):
    customer = get_prospective_customer(request.tenant, customer_id)
    if request.method != "POST":
        return redirect("customers:prospective_detail", customer_id=customer_id)
    form = InteractionForm(request.POST)
    if form.is_valid():
        record_customer_interaction(
            tenant=request.tenant, customer=customer, user=request.user,
            cleaned_data=form.cleaned_data,
        )
        messages.success(request, "نتیجه ارتباط و پیگیری بعدی ثبت شد.")
        return redirect("customers:prospective_detail", customer_id=customer_id)
    messages.error(request, "ثبت ارتباط انجام نشد؛ اطلاعات فرم را بررسی کنید.")
    return render(
        request, "customers/prospective_detail.html",
        _detail_context(request, customer, interaction_form=form),
        status=400,
    )


@tenant_required
@role_required("COMPANY_ADMIN")
def prospective_convert(request, customer_id):
    customer = get_prospective_customer(request.tenant, customer_id)
    if request.method == "POST":
        try:
            convert_to_active_customer(tenant=request.tenant, customer=customer, user=request.user)
        except ValidationError as error:
            messages.error(request, "؛ ".join(error.messages))
            return redirect("customers:prospective_detail", customer_id=customer_id)
        messages.success(request, f"«{customer.industrial_unit.official_name}» به مشتری فعال تبدیل شد.")
        return redirect("customers:active_detail", customer_id=customer_id)
    return render(request, "customers/prospective_convert.html", {"customer": customer})


@tenant_required
def active_list(request):
    base = active_customers_for_tenant(request.tenant)
    customer_ids = base.values("tenant_customer_id")
    sale_contracts = Contract.objects.for_tenant(request.tenant).filter(
        contract_direction="SALE",
        tenant_customer_id__in=customer_ids,
    )
    stats = {
        "total": base.count(),
        "contracts": sale_contracts.count(),
        "contracts_with_dates": sale_contracts.filter(
            contract_start_gregorian__isnull=False,
            contract_end_gregorian__isnull=False,
        ).count(),
        "contracts_missing_dates": sale_contracts.filter(
            Q(contract_start_gregorian__isnull=True)
            | Q(contract_end_gregorian__isnull=True)
        ).count(),
        "without_contact": base.filter(has_contact=False).count(),
        "review_required": filter_information(base, "ATTENTION").count(),
    }
    filters = {
        "query": request.GET.get("q", ""),
        "contract_data": request.GET.get("contract_data", ""),
        "quality": "",  # E04: retired UI filter; ignore old bookmarked query values.
        "ordering": request.GET.get("order", "name"),
    }
    queryset = filter_active_customers(base, **filters)
    filters["cycle"] = request.GET.get("cycle", "")
    if filters["cycle"] not in CYCLES:
        filters["cycle"] = ""
    queryset = classify_customers(queryset, request.tenant, filters["cycle"])
    filters["ownership"] = normalize_ownership(request.GET.get("ownership", ""))
    if filters["ownership"]:
        queryset = queryset.filter(ownership_code=filters["ownership"])
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page.object_list = attach_provisional_ranks(
        request.tenant, attach_customer_information(list(page.object_list))
    )
    return render(
        request,
        "customers/active_list.html",
        {"page": page, "stats": stats, "filters": filters},
    )


@tenant_required
@require_GET
def provisional_rank_list(request):
    """Display-only, tenant-scoped provisional ranking and approved filters."""
    rank = request.GET.get("rank", "")
    if rank not in ("", "1", "2", "3", "4", "5", "unknown"):
        return HttpResponseBadRequest("فیلتر رتبه معتبر نیست.")
    query = request.GET.get("q", "").strip()
    queryset = filter_active_customers(
        active_customers_for_tenant(request.tenant), query=query, ordering="contracts"
    )
    if rank == "unknown":
        queryset = queryset.filter(contract_count=0)
    elif rank == "5":
        queryset = queryset.filter(contract_count__gte=5)
    elif rank:
        queryset = queryset.filter(contract_count=int(rank))
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page.object_list = attach_provisional_ranks(request.tenant, list(page.object_list))
    return render(request, "customers/provisional_rank_list.html", {
        "page": page, "filters": {"query": query, "rank": rank},
    })


@tenant_required
def active_detail(request, customer_id):
    customer = get_active_customer(request.tenant, customer_id)
    attach_provisional_ranks(request.tenant, [customer])
    contacts = list(CustomerContact.objects.for_tenant(request.tenant).filter(
        tenant_customer=customer
    ))
    attach_business_classification(customer, request.tenant)
    customer.information = assess_customer(customer, contacts)
    contract_data = active_contract_rows(request.tenant, customer)
    return render(
        request,
        "customers/active_detail.html",
        {
            "customer": customer,
            "contacts": contacts,
            "contract_data": contract_data,
        },
    )


@tenant_required
def dormant_list(request):
    base = dormant_customers_for_tenant(request.tenant)
    stats = {
        "total": base.count(),
        "from_active": base.filter(previous_customer_status="ACTIVE").count(),
        "from_lead": base.filter(previous_customer_status="LEAD").count(),
        "without_interaction": base.filter(last_interaction_at__isnull=True).count(),
    }
    filters = {
        "query": request.GET.get("q", ""),
        "reason_code": request.GET.get("reason", ""),
        "previous_status": request.GET.get("previous_status", ""),
        "ordering": request.GET.get("order", "latest"),
    }
    queryset = filter_dormant_customers(
        base, tenant=request.tenant, **filters
    )
    filters["cycle"] = request.GET.get("cycle", "")
    if filters["cycle"] not in CYCLES:
        filters["cycle"] = ""
    queryset = classify_customers(queryset, request.tenant, filters["cycle"])
    filters["ownership"] = normalize_ownership(request.GET.get("ownership", ""))
    if filters["ownership"]:
        queryset = queryset.filter(ownership_code=filters["ownership"])
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "customers/dormant_list.html",
        {"page": page, "stats": stats, "filters": filters},
    )


@tenant_required
def dormant_detail(request, customer_id):
    customer = get_dormant_customer(request.tenant, customer_id)
    attach_business_classification(customer, request.tenant)
    contacts = CustomerContact.objects.for_tenant(request.tenant).filter(
        tenant_customer=customer
    )
    latest_dormant = CustomerStatusHistory.objects.for_tenant(request.tenant).filter(
        tenant_customer_id=customer.tenant_customer_id,
        new_status="DORMANT",
    ).first()
    return render(
        request,
        "customers/dormant_detail.html",
        {
            "customer": customer,
            "contacts": contacts,
            "interaction_rows": interaction_rows_for_customer(
                request.tenant, customer
            ),
            "history_rows": customer_status_history_rows(
                request.tenant, customer
            ),
            "latest_dormant": latest_dormant,
            "restore_form": DormantRestoreForm(initial={"target_status": "LEAD"}),
        },
    )


@tenant_required
@role_required("COMPANY_ADMIN")
def customer_mark_dormant(request, customer_id):
    customer = get_status_change_candidate(request.tenant, customer_id)
    form = DormantTransitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            customer = mark_customer_dormant(
                tenant=request.tenant,
                customer_id=customer_id,
                user=request.user,
                reason_code=form.cleaned_data["reason_code"],
                reason_note=form.cleaned_data.get("reason_note", ""),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                f"«{customer.industrial_unit.official_name}» به فهرست مشترکین راکد منتقل شد.",
            )
            return redirect("customers:dormant_detail", customer_id=customer_id)
    return render(
        request,
        "customers/mark_dormant.html",
        {"customer": customer, "form": form},
    )


@tenant_required
@role_required("COMPANY_ADMIN")
def dormant_restore(request, customer_id):
    customer = get_dormant_customer(request.tenant, customer_id)
    attach_business_classification(customer, request.tenant)
    if request.method != "POST":
        return redirect("customers:dormant_detail", customer_id=customer_id)
    form = DormantRestoreForm(request.POST)
    if not form.is_valid():
        contacts = CustomerContact.objects.for_tenant(request.tenant).filter(
            tenant_customer=customer
        )
        latest_dormant = CustomerStatusHistory.objects.for_tenant(
            request.tenant
        ).filter(
            tenant_customer_id=customer.tenant_customer_id,
            new_status="DORMANT",
        ).first()
        messages.error(request, "وضعیت مقصد را انتخاب کنید.")
        return render(
            request,
            "customers/dormant_detail.html",
            {
                "customer": customer,
                "contacts": contacts,
                "interaction_rows": interaction_rows_for_customer(
                    request.tenant, customer
                ),
                "history_rows": customer_status_history_rows(
                    request.tenant, customer
                ),
                "latest_dormant": latest_dormant,
                "restore_form": form,
            },
            status=400,
        )
    target_status = form.cleaned_data["target_status"]
    try:
        customer = restore_dormant_customer(
            tenant=request.tenant,
            customer_id=customer_id,
            user=request.user,
            target_status=target_status,
            reason_note=form.cleaned_data.get("reason_note", ""),
        )
    except ValidationError as error:
        messages.error(request, "؛ ".join(error.messages))
        return redirect("customers:dormant_detail", customer_id=customer_id)
    messages.success(request, "وضعیت مشتری با موفقیت بازگردانی شد.")
    if target_status == "ACTIVE":
        return redirect("customers:active_detail", customer_id=customer.tenant_customer_id)
    return redirect(
        "customers:prospective_detail", customer_id=customer.tenant_customer_id
    )


@tenant_required
def customer_profile(request, customer_id):
    customer = get_customer_for_profile(request.tenant, customer_id)
    if customer.customer_status == "ACTIVE":
        attach_provisional_ranks(request.tenant, [customer])
    attach_business_classification(customer, request.tenant)
    profile = build_customer_profile(request.tenant, customer)
    return render(
        request,
        "customers/customer_profile.html",
        {"customer": customer, "profile": profile, "source_notes": source_notes_for_customer(request.tenant, customer), "excel_source_rows": workbook_rows_for_customer(request.tenant, customer)},
    )
