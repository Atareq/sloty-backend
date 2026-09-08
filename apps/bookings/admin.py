from django.contrib import admin

from apps.bookings.models import Booking, BookingAttempt


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "court",
        "club",
        "customer_name",
        "customer_phone",
        "start_time",
        "end_time",
        "total_price",
        "status",
        "source",
        "created_by",
    )
    list_filter = ("status", "source", "club", "court")
    search_fields = (
        "customer_name",
        "customer_phone",
        "court__name",
        "club__name",
    )
    date_hierarchy = "start_time"
    readonly_fields = ("total_price", "created_by", "created", "modified")


@admin.register(BookingAttempt)
class BookingAttemptAdmin(admin.ModelAdmin):
    historical_readonly_fields = (
        "club",
        "court",
        "attempted_by",
        "booking",
        "client_request_id",
        "customer_name",
        "customer_phone",
        "notes",
        "requested_start",
        "requested_end",
        "requested_at",
        "requested_source",
        "requested_recurring",
        "outcome",
        "failure_code",
        "failure_details",
    )
    list_display = (
        "id",
        "court",
        "club",
        "customer_name",
        "requested_start",
        "requested_end",
        "requested_source",
        "outcome",
        "resolution",
        "attempted_by",
        "booking",
    )
    list_filter = (
        "outcome",
        "resolution",
        "requested_source",
        "club",
        "court",
        "requested_recurring",
    )
    search_fields = (
        "client_request_id",
        "customer_name",
        "customer_phone",
        "failure_code",
        "court__name",
        "club__name",
    )
    date_hierarchy = "requested_at"
    readonly_fields = ("created", "modified")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return self.readonly_fields + self.historical_readonly_fields
