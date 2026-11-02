from django.urls import path

from . import views

app_name = "interactions"

urlpatterns = [
    path("alerts/", views.alert_center, name="alert_center"),
    path("alerts/sync/", views.alert_sync, name="alert_sync"),
    path("alerts/<int:alert_id>/action/", views.alert_action, name="alert_action"),
    path("", views.work_queue, name="work_queue"),
    path("new/", views.interaction_create, name="create"),
    path(
        "customer/<int:customer_id>/new/",
        views.interaction_create,
        name="create_for_customer",
    ),
    path(
        "follow-ups/<int:follow_up_id>/action/",
        views.follow_up_action,
        name="follow_up_action",
    ),
    path(
        "follow-ups/<int:follow_up_id>/reschedule/",
        views.follow_up_reschedule,
        name="follow_up_reschedule",
    ),
]
