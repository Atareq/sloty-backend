from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "club",
        "action",
        "entity_type",
        "entity_id",
        "actor",
        "court",
        "created",
    )
    list_filter = ("club", "action", "entity_type", "created")
    search_fields = (
        "club__name",
        "club__slug",
        "actor__username",
        "actor__first_name",
        "actor__last_name",
        "entity_type",
        "entity_id",
    )
    readonly_fields = (
        "club",
        "court",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "before_data",
        "after_data",
        "metadata",
        "created",
    )
