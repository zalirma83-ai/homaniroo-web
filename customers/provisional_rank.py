"""Read-only P07 ranking of active customers by scoped sale contract records."""
from collections import defaultdict

from django.core.exceptions import PermissionDenied
from django.db.models import Count, TextField, Value
from django.db.models.functions import Coalesce, Lower, Trim

from contracts.models import Contract


def _tenant_id(tenant):
    value = getattr(tenant, 'tenant_id', tenant)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PermissionDenied
    return value


def attach_provisional_ranks(tenant, customers):
    """Attach a fresh, display-only rank; never write or deduplicate records.

    Caller obtains scoped customer records; reject cross-company objects rather
    than returning an apparently valid score. One grouped query per page.
    """
    tenant_id = _tenant_id(tenant)
    customers = list(customers)
    for customer in customers:
        if getattr(customer, 'tenant_id', None) != tenant_id:
            raise PermissionDenied
    active = [c for c in customers if c.customer_status == 'ACTIVE']
    for customer in customers:
        customer.provisional_rank = None
    if not active:
        return customers
    ids = [c.tenant_customer_id for c in active]
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids):
        raise PermissionDenied
    rows = (Contract.objects.filter(tenant_id=tenant_id, tenant_customer_id__in=ids,
                                    contract_direction='SALE')
        .annotate(number_key=Lower(Trim(Coalesce('contract_number', Value('', output_field=TextField())))))
        .values('tenant_customer_id', 'number_key')
        .annotate(record_count=Count('contract_id'))
        .order_by())
    aggregates = defaultdict(lambda: {'count': 0, 'missing_number': False, 'repeated_number': False})
    for row in rows:
        item = aggregates[row['tenant_customer_id']]
        item['count'] += row['record_count']
        if not row['number_key']:
            item['missing_number'] = True
        elif row['record_count'] > 1:
            item['repeated_number'] = True
    for customer in active:
        item = aggregates[customer.tenant_customer_id]
        customer.provisional_rank = {
            'value': min(item['count'], 5) if item['count'] else None,
            'contract_count': item['count'],
            'missing_number': item['missing_number'],
            'repeated_number': item['repeated_number'],
        }
    return customers
