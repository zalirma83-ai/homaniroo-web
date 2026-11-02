from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_center, name="center"),
    path("export/<str:report_key>/", views.export_csv, name="export_csv"),
]
