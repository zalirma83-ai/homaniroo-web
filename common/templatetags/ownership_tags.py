from django import template
from customers.ownership import LABELS
register = template.Library()

@register.filter
def ownership_label(value):
    return LABELS.get(value, "نامشخص")
