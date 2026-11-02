from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from accounts.access import role_required
from common.tenant_access import tenant_required
from .models import OWNERSHIP_CHOICES
from .ownership import EDIT_ROLES, accessible_unit, set_ownership


class OwnershipForm(forms.Form):
    ownership_type = forms.ChoiceField(label="نوع مالکیت", choices=OWNERSHIP_CHOICES)


@tenant_required
@role_required(*EDIT_ROLES)
@require_POST
def ownership_update(request, unit_id):
    unit = accessible_unit(request.tenant, unit_id)
    form = OwnershipForm(request.POST)
    destination = request.POST.get("next", "")
    if not destination or not url_has_allowed_host_and_scheme(destination, {request.get_host()}, require_https=request.is_secure()):
        destination = reverse("customers:partner_list")
    if form.is_valid():
        set_ownership(tenant=request.tenant, unit_id=unit_id, value=form.cleaned_data["ownership_type"], user=request.user)
        messages.success(request, "نوع مالکیت ذخیره شد؛ نمایش‌های مرتبط از همین مقدار استفاده می‌کنند.")
        return redirect(destination)
    return render(request, "customers/ownership_edit_error.html", {"form": form, "unit": unit, "return_url": destination}, status=400)
