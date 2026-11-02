from django.contrib import admin

from .models import CoreUser, Role, Tenant, TenantMembership, UserRoleAssignment


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("tenant_id", "tenant_code", "display_name", "status")
    search_fields = ("tenant_code", "legal_name", "display_name")
    list_filter = ("status",)


@admin.register(CoreUser)
class CoreUserAdmin(admin.ModelAdmin):
    list_display = ("user_id", "username", "full_name", "account_status")
    search_fields = ("username", "full_name", "email")
    list_filter = ("account_status",)

    def get_readonly_fields(self, request, obj=None):
        # Linked login usernames must be synchronized through the credential form.
        return ("username",) if obj else ()


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("role_id", "role_code", "role_name_fa", "is_system_role")
    search_fields = ("role_code", "role_name_fa")


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("tenant_user_id", "tenant", "core_user", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("core_user__username", "core_user__full_name")


@admin.register(UserRoleAssignment)
class UserRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("core_user", "role", "tenant", "assigned_at")
    list_filter = ("role", "tenant")
    search_fields = ("core_user__username", "core_user__full_name")
