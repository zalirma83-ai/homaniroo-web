from django.shortcuts import render

from accounts.access import role_required
from common.tenant_access import tenant_required
from .services import build_energy_trading_context


OPERATIONAL_ROLES = ("COMPANY_ADMIN", "SENIOR_OPERATOR", "OPERATOR")


@tenant_required
@role_required(*OPERATIONAL_ROLES)
def trading_dashboard(request):
    context = build_energy_trading_context(
        request.tenant,
        scope=request.GET.get("scope", "CONTRACT_TOTAL"),
        period=request.GET.get("period", ""),
        energy_type=request.GET.get("energy", ""),
        load_band=request.GET.get("load", ""),
        status=request.GET.get("status", ""),
        completeness=request.GET.get("completeness", ""),
    )
    return render(request, "energy/trading_dashboard.html", context)
