"""Tenant-scoped, read-only access to the existing business classification table."""
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404

from .models import EntityBusinessProfile, TenantCustomer
from .ownership import annotate_ownership, attach_ownership

CYCLES = {"ANNUAL", "MONTHLY", "UNCLASSIFIED"}


def confirmed_profiles(tenant):
    return EntityBusinessProfile.objects.for_tenant(tenant).filter(
        classification_status="CONFIRMED",
    )


def classify_customers(queryset, tenant, cycle=""):
    queryset = annotate_ownership(queryset.for_tenant(tenant), tenant)
    profiles = confirmed_profiles(tenant).filter(
        industrial_unit_id=OuterRef("industrial_unit_id"),
    ).order_by("profile_id")
    queryset = queryset.annotate(
        business_relationship=Subquery(profiles.values("relationship_type")[:1]),
        business_cycle=Subquery(profiles.values("purchase_cycle")[:1]),
    )
    if cycle in {"ANNUAL", "MONTHLY"}:
        return queryset.filter(business_relationship="CUSTOMER", business_cycle=cycle)
    if cycle == "UNCLASSIFIED":
        known = confirmed_profiles(tenant).filter(
            relationship_type="CUSTOMER", purchase_cycle__in=["ANNUAL", "MONTHLY"],
        ).values("industrial_unit_id")
        return queryset.exclude(industrial_unit_id__in=known)
    return queryset


def attach_business_classification(customer, tenant):
    profile = confirmed_profiles(tenant).filter(industrial_unit_id=customer.industrial_unit_id).first()
    customer.business_relationship = profile.relationship_type if profile else None
    customer.business_cycle = profile.purchase_cycle if profile else None
    attach_ownership([customer], tenant)
    return customer


def partners_for_tenant(tenant, query=""):
    members = TenantCustomer.objects.for_tenant(tenant).filter(
        industrial_unit_id=OuterRef("industrial_unit_id"),
    ).order_by("tenant_customer_id")
    partners = confirmed_profiles(tenant).filter(relationship_type="PARTNER").select_related(
        "industrial_unit",
    ).annotate(customer_id=Subquery(members.values("tenant_customer_id")[:1]))
    query = query.strip()
    if query:
        partners = partners.filter(
            Q(display_name__icontains=query)
            | Q(industrial_unit__official_name__icontains=query)
            | Q(provided_national_id__icontains=query)
        )
    return annotate_ownership(partners, tenant).order_by("display_name", "profile_id")


def get_partner(tenant, profile_id):
    partner = get_object_or_404(partners_for_tenant(tenant), profile_id=profile_id)
    attach_ownership([partner], tenant)
    return partner
