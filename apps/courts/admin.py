from django.contrib import admin

from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod


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


@admin.register(CourtWorkingHourPricePeriod)
class CourtWorkingHourPricePeriodAdmin(admin.ModelAdmin):
    list_display = ("working_hour", "starts_at", "ends_at", "price")
    list_filter = ("working_hour__weekday",)
    search_fields = ("working_hour__court__name", "working_hour__court__club__name")
