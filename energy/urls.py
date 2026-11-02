from django.urls import path

from . import views

app_name = "energy"

urlpatterns = [
    path("", views.trading_dashboard, name="trading_dashboard"),
]
