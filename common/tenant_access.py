from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def tenant_required(view_func):
    """صفحه عملیاتی را بدون شرکت فعال اجرا نمی‌کند."""

    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.tenant is None:
            messages.info(request, "ابتدا شرکت فعال را انتخاب کنید.")
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"/companies/select/?{query}")
        return view_func(request, *args, **kwargs)

    return wrapped
