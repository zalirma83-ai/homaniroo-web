from django.urls import path

from . import views

app_name = "auditlog"

urlpatterns = [
    path("", views.audit_center, name="center"),
    path("export/", views.audit_export, name="export"),
]
