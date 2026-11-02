from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.access import role_required
from .services import build_dashboard_context


@login_required
def home(request):
    context = build_dashboard_context(request.tenant)
    return render(request, "dashboard/home.html", context)


@role_required("SUPERADMIN")
def system_control(request):
    return render(request, "dashboard/system_control.html")
