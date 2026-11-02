from django.urls import path

from . import business_views, ownership_views, views

app_name = "customers"

urlpatterns = [
    path("ownership/<int:unit_id>/", ownership_views.ownership_update, name="ownership_update"),
    path("partners/", business_views.partner_list, name="partner_list"),
    path("partners/<int:profile_id>/", business_views.partner_detail, name="partner_detail"),
    path("partners/<int:profile_id>/note/", business_views.partner_note_update, name="partner_note_update"),
    path(
        "profile/<int:customer_id>/",
        views.customer_profile,
        name="customer_profile",
    ),
    path("dormant/", views.dormant_list, name="dormant_list"),
    path("dormant/<int:customer_id>/", views.dormant_detail, name="dormant_detail"),
    path(
        "dormant/<int:customer_id>/restore/",
        views.dormant_restore,
        name="dormant_restore",
    ),
    path(
        "status/<int:customer_id>/mark-dormant/",
        views.customer_mark_dormant,
        name="mark_dormant",
    ),
    path("ranking/", views.provisional_rank_list, name="provisional_rank_list"),
    path("active/", views.active_list, name="active_list"),
    path("active/<int:customer_id>/", views.active_detail, name="active_detail"),
    path("prospective/", views.prospective_list, name="prospective_list"),
    path("prospective/create/", views.prospective_create, name="prospective_create"),
    path("prospective/<int:customer_id>/", views.prospective_detail, name="prospective_detail"),
    path("prospective/<int:customer_id>/edit/", views.prospective_edit, name="prospective_edit"),
    path("prospective/<int:customer_id>/contacts/add/", views.contact_add, name="contact_add"),
    path("prospective/<int:customer_id>/interactions/add/", views.interaction_add, name="interaction_add"),
    path("prospective/<int:customer_id>/convert/", views.prospective_convert, name="prospective_convert"),
]
