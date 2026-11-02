from django.db import models


class TenantScopedQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        """فقط رکوردهای شرکت فعال را برمی‌گرداند؛ بدون شرکت نتیجه خالی است."""
        if tenant is None:
            return self.none()
        tenant_id = getattr(tenant, "tenant_id", tenant)
        return self.filter(tenant_id=tenant_id)


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):
    pass
