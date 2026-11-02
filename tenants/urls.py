from django.urls import path

from . import operator_views, views

app_name = "tenants"

urlpatterns = [
    path("operators/", operator_views.operator_list, name="operator_list"),
    path("operators/create/", operator_views.operator_create, name="operator_create"),
    path("operators/<int:user_id>/edit/", operator_views.operator_edit, name="operator_edit"),
    path("data-preview/", views.tenant_data_preview, name="data_preview"),
    path("users/", views.company_user_list, name="user_list"),
    path("users/create/", views.company_user_create, name="user_create"),
    path(
        "users/<int:core_user_id>/edit/",
        views.company_user_update,
        name="user_update",
    ),
    path("", views.tenant_list, name="list"),
    path("create/", views.tenant_create, name="create"),
    path("<int:tenant_id>/edit/", views.tenant_update, name="update"),
    path("<int:tenant_id>/delete/", views.tenant_delete, name="delete"),
    path("select/", views.select_tenant, name="select"),
    path("clear/", views.clear_tenant, name="clear"),
]
