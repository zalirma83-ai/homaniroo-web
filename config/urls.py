from django.contrib import admin
from django.urls import include, path

from common.health import health_check

handler400 = "common.error_views.bad_request"
handler403 = "common.error_views.permission_denied"
handler404 = "common.error_views.page_not_found"
handler500 = "common.error_views.server_error"

urlpatterns = [
    path("health/", health_check, name="health"),
    path("admin/", admin.site.urls),
    path("account/", include("accounts.urls")),
    path("companies/", include("tenants.urls")),
    path("customers/", include("customers.urls")),
    path("contracts/", include("contracts.urls")),
    path("energy/", include("energy.urls")),
    path("interactions/", include("interactions.urls")),
    path("reports/", include("reports.urls")),
    path("system/audit/", include("auditlog.urls")),
    path("", include("dashboard.urls")),
]
