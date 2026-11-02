from django.urls import path

from . import views

app_name = "contracts"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("<int:contract_id>/", views.contract_detail, name="detail"),
]
