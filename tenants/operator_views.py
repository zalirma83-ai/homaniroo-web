from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from accounts.access import role_required
from common.tenant_access import tenant_required
from .operator_forms import OperatorCreateForm, OperatorEditForm
from .operator_services import (OperatorAuditUnavailable, OperatorConflict,
                                create_operator, list_operators, operator_edit_state, update_operator)


@tenant_required
@role_required('COMPANY_ADMIN')
@never_cache
@require_http_methods(['GET'])
def operator_list(request):
    rows = list_operators(actor=request.user, tenant=request.tenant)
    return render(request, 'tenants/operator_list.html', {'rows': rows})


@sensitive_post_parameters()
@tenant_required
@role_required('COMPANY_ADMIN')
@never_cache
@require_http_methods(['GET', 'POST'])
def operator_create(request):
    form = OperatorCreateForm(request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                create_operator(actor=request.user, tenant=request.tenant,
                    username=form.cleaned_data['username'], full_name=form.cleaned_data['full_name'],
                    email=form.cleaned_data['email'], password=form.cleaned_data['password1'], request=request)
            except ValidationError as error:
                form.add_error(None, error)
            except (IntegrityError, OperatorAuditUnavailable):
                form.add_error(None, 'ساخت حساب انجام نشد؛ اطلاعات یا ثبت ممیزی نیاز به بررسی دارد.')
            else:
                messages.success(request, 'حساب اپراتور در شرکت فعال ایجاد شد.')
                return redirect('tenants:operator_list')
    return render(request, 'tenants/operator_form.html', {'form': form, 'page_title': 'ایجاد اپراتور'}, status=status)


@sensitive_post_parameters()
@tenant_required
@role_required('COMPANY_ADMIN')
@never_cache
@require_http_methods(['GET', 'POST'])
def operator_edit(request, user_id):
    state = operator_edit_state(actor=request.user, tenant=request.tenant, user_id=user_id)
    form = OperatorEditForm(request.POST if request.method == 'POST' else None,
                            target_user=state['user'], core=state['core'], edit_token=state['token'])
    context = {'form': form, 'page_title': 'ویرایش اپراتور'}
    status = 200
    if request.method == 'POST':
        status = 400
        if form.is_valid():
            try:
                _, changed = update_operator(actor=request.user, tenant=request.tenant, user_id=user_id,
                    full_name=form.cleaned_data['full_name'], username=form.cleaned_data['username'],
                    account_status=form.cleaned_data['account_status'],
                    new_password=form.cleaned_data.get('new_password1', ''),
                    edit_token=form.cleaned_data['edit_token'], request=request)
            except OperatorConflict as error:
                form.add_error(None, error); context['edit_blocked'] = True; status = 409
            except ValidationError as error:
                form.add_error(None, error)
            except (IntegrityError, OperatorAuditUnavailable):
                form.add_error(None, 'ویرایش ذخیره نشد؛ یکپارچگی اطلاعات یا ثبت ممیزی نیاز به بررسی دارد.')
                context['edit_blocked'] = True
            else:
                messages.success(request, 'اطلاعات اپراتور ذخیره شد.' if changed else 'تغییری در اطلاعات اپراتور نبود.')
                return redirect('tenants:operator_list')
    return render(request, 'tenants/operator_form.html', context, status=status)
