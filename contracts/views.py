from django.core.paginator import Paginator
from django.shortcuts import render

from accounts.access import role_required
from common.tenant_access import tenant_required
from .services import build_contract_list, enrich_contracts, get_contract_detail


OPERATIONAL_ROLES = ("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def contract_list(request):
    context = build_contract_list(
        request.tenant,
        query=request.GET.get("q", ""),
        direction=request.GET.get("direction", ""),
        transaction_type=request.GET.get("transaction", ""),
        quality="",  # E04: retired UI filter; ignore old bookmarked query values.
        energy_type=request.GET.get("energy", ""),
        date_state=request.GET.get("date", ""),
        ordering=request.GET.get("order", "newest"),
        ownership=request.GET.get("ownership", ""),
    )
    page = Paginator(context.pop("queryset"), 25).get_page(request.GET.get("page"))
    context["page"] = page
    context["rows"] = enrich_contracts(request.tenant, page.object_list)
    return render(request, "contracts/contract_list.html", context)


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def contract_detail(request, contract_id):
    context = get_contract_detail(request.tenant, contract_id)
    return render(request, "contracts/contract_detail.html", context)
