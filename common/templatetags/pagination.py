"""Numbered pagination that preserves the current GET filters without DB queries."""
from django import template
from django.http import QueryDict

register = template.Library()


@register.inclusion_tag("partials/pagination.html", takes_context=True)
def numbered_pagination(context, page, page_param="page", aria_label="صفحه‌بندی", ignored_params=""):
    if page is None or page.paginator.num_pages <= 1:
        return {"show_pagination": False}

    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict("", mutable=True)
    params.pop(page_param, None)
    # Opt-in removal: other pages retain all their existing GET parameters.
    for name in ignored_params.split(","):
        if name.strip():
            params.pop(name.strip(), None)

    def page_url(number):
        query = params.copy()
        query[page_param] = str(number)
        return "?" + query.urlencode()

    page_links = []
    for number in page.paginator.get_elided_page_range(
        page.number, on_each_side=2, on_ends=1
    ):
        is_gap = number == page.paginator.ELLIPSIS
        page_links.append({
            "number": number,
            "is_gap": is_gap,
            "is_current": not is_gap and number == page.number,
            "url": None if is_gap else page_url(number),
        })

    return {
        "show_pagination": True,
        "page_parameter": page_param,
        "pagination_label": aria_label,
        "page_number": page.number,
        "total_pages": page.paginator.num_pages,
        "page_links": page_links,
        "first_url": page_url(1) if page.has_previous() else None,
        "previous_url": page_url(page.previous_page_number()) if page.has_previous() else None,
        "next_url": page_url(page.next_page_number()) if page.has_next() else None,
        "last_url": page_url(page.paginator.num_pages) if page.has_next() else None,
        "hidden_filters": [
            (key, value)
            for key, values in params.lists()
            for value in values
        ],
        "pagination_path": request.path if request is not None else "",
    }
