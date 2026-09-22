from django.contrib import admin
from django.utils import timezone

from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.services import hold_expiry_deadline


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "court",
        "club",
        "club_player",
        "start_time",
        "end_time",
        "total_price",
        "status",
        "source",
        "created_by",
    )
    list_filter = ("status", "source", "club", "court")
    search_fields = (
        "club_player__display_name",
        "club_player__player_profile__phone_number",
        "court__name",
        "club__name",
    )
    date_hierarchy = "start_time"
    readonly_fields = (
        "total_price",
        "created_by",
        "created",
        "modified",
        "hold_expires_at",
    )

    def save_model(self, request, obj, form, change):
        starts_hold = obj.status == Booking.Status.HOLD and (
            not change or form.initial.get("status") != Booking.Status.HOLD
        )
        missing_deadline = (
            obj.status == Booking.Status.HOLD and obj.hold_expires_at is None
        )
        super().save_model(request, obj, form, change)
        if starts_hold or missing_deadline:
            from_time = obj.created if not change else timezone.now()
            obj.hold_expires_at = hold_expiry_deadline(obj.court, from_time=from_time)
            obj.save(update_fields=["hold_expires_at"])


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
    readonly_fields = ("created",)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return self.readonly_fields + self.historical_readonly_fields
