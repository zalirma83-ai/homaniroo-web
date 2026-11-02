from django.contrib import admin

from .models import AuditLog
from .outcomes import RESULT_FILTER_LABELS, filter_audit_result, with_audit_outcomes
from .services import ACTION_LABELS


class AuditResultFilter(admin.SimpleListFilter):
    title = "نتیجه و نوع ثبت"
    parameter_name = "result"

    def lookups(self, request, model_admin):
        return tuple(RESULT_FILTER_LABELS.items())

    def queryset(self, request, queryset):
        return filter_audit_result(queryset, self.value())


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "event_action", "tenant_id", "event_result", "severity")
    list_filter = ("action", AuditResultFilter, "severity")
    search_fields = ("username", "description", "entity_type", "entity_id", "request_path")
    exclude = ("success",)
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields if field.name != "success") + ("event_result",)

    def get_queryset(self, request):
        return with_audit_outcomes(super().get_queryset(request), ACTION_LABELS)

    @admin.display(description="نوع ثبت")
    def event_action(self, obj):
        return obj.audit_action_label

    @admin.display(description="نتیجهٔ ثبت‌شده؛ HTTP اثبات عملیات نیست")
    def event_result(self, obj):
        return obj.audit_result_label

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
