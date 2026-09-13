from django.contrib import admin

from apps.transactions.models import Transaction, TransactionAttempt


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "booking",
        "club",
        "court",
        "amount",
        "client_request_id",
        "payment_method",
        "payment_reference",
        "occurred_at",
        "created_by",
        "is_cancelled",
        "cancelled_by",
        "cancelled_at",
        "created",
    )
    list_filter = (
        "payment_method",
        "is_cancelled",
        "club",
        "court",
        "occurred_at",
        "created",
    )
    search_fields = (
        "client_request_id",
        "payment_reference",
        "booking__customer_name",
        "booking__customer_phone",
        "booking__club_player__display_name",
        "booking__club_player__player_profile__phone_number",
        "court__name",
        "club__name",
        "club__slug",
        "created_by__username",
        "cancellation_reason",
    )
    readonly_fields = (
        "club",
        "court",
        "booking",
        "amount",
        "client_request_id",
        "payment_method",
        "payment_reference",
        "occurred_at",
        "created_by",
        "is_cancelled",
        "cancelled_by",
        "cancelled_at",
        "cancellation_reason",
        "created",
        "modified",
    )


@admin.register(TransactionAttempt)
class TransactionAttemptAdmin(admin.ModelAdmin):
    historical_readonly_fields = (
        "club",
        "court",
        "booking",
        "attempted_by",
        "transaction",
        "client_request_id",
        "amount",
        "payment_method",
        "payment_reference",
        "notes",
        "occurred_at",
        "outcome",
        "failure_code",
        "failure_details",
        "created",
        "modified",
    )
    list_display = (
        "id",
        "booking",
        "club",
        "court",
        "amount",
        "payment_method",
        "outcome",
        "resolution",
        "failure_code",
        "transaction",
        "attempted_by",
        "client_request_id",
        "occurred_at",
        "created",
    )
    list_filter = (
        "outcome",
        "resolution",
        "payment_method",
        "failure_code",
        "club",
        "court",
        "occurred_at",
        "created",
    )
    search_fields = (
        "client_request_id",
        "payment_reference",
        "booking__customer_name",
        "booking__customer_phone",
        "booking__club_player__display_name",
        "booking__club_player__player_profile__phone_number",
        "court__name",
        "club__name",
        "club__slug",
        "attempted_by__username",
        "failure_code",
    )
    readonly_fields = historical_readonly_fields + ("resolution",)
