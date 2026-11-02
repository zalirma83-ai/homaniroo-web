from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from common.tenant_access import tenant_required
from .business_services import get_partner, partners_for_tenant
from .ownership import normalize
from .partner_note_forms import PartnerNoteForm
from .partner_notes import NoteConflict, can_edit_partner_note, read_partner_note, save_partner_note


@tenant_required
@require_safe
def partner_list(request):
    query = request.GET.get("q", "").strip()
    base = partners_for_tenant(request.tenant)
    ownership = normalize(request.GET.get("ownership", ""))
    queryset = partners_for_tenant(request.tenant, query)
    if ownership:
        queryset = queryset.filter(ownership_code=ownership)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "customers/partner_list.html", {
        "page": page, "query": query, "total": base.count(), "ownership_filter": ownership,
    })


@tenant_required
@require_safe
def partner_detail(request, profile_id):
    partner = get_partner(request.tenant, profile_id)
    return render(request, "customers/partner_detail.html", _partner_context(request, partner))


def _partner_context(request, partner, *, form=None):
    note = read_partner_note(request.tenant, partner.pk)
    permitted = can_edit_partner_note(request.user, request.tenant)
    return {
        "partner": partner, "operator_note": note, "can_edit_note": permitted,
        "note_form": form if form is not None else PartnerNoteForm(initial={
            "body": note.body if note else "", "version": note.version if note else 0,
        }),
    }


@tenant_required
@require_POST
def partner_note_update(request, profile_id):
    if not can_edit_partner_note(request.user, request.tenant):
        raise PermissionDenied
    partner = get_partner(request.tenant, profile_id)
    form = PartnerNoteForm(request.POST)
    status = 400
    if form.is_valid():
        try:
            _, changed = save_partner_note(
                tenant=request.tenant, profile_id=profile_id, body=form.cleaned_data["body"],
                expected_version=form.cleaned_data["version"], user=request.user,
            )
        except NoteConflict:
            form.add_error(None, "یادداشت از زمان بازکردن صفحه تغییر کرده است. متن ارسالی شما حفظ شده؛ آن را کپی کنید و صفحه را دوباره باز کنید. چیزی بازنویسی نشد.")
            status = 409
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "یادداشت همکار ذخیره شد." if changed else "یادداشت تغییری نکرد.")
            return redirect(reverse("customers:partner_detail", args=[profile_id]) + "#operator-note")
    return render(request, "customers/partner_detail.html", _partner_context(request, partner, form=form), status=status)
