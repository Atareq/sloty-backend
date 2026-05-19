from django.contrib import admin

from apps.courts.models import Court, CourtWorkingHour


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "club",
        "sport_type",
        "default_price",
        "slot_duration_minutes",
        "is_active",
    )
    list_filter = (
        "sport_type",
        "is_active",
        "requires_digital_payment_reference",
    )
    search_fields = ("name", "club__name")


@admin.register(CourtWorkingHour)
class CourtWorkingHourAdmin(admin.ModelAdmin):
    list_display = ("court", "weekday", "opens_at", "closes_at", "is_closed")
    list_filter = ("weekday", "is_closed")
