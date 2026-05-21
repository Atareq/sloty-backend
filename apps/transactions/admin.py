from django.contrib import admin

from apps.transactions.models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "booking",
        "club",
        "court",
        "amount",
        "payment_method",
        "payment_reference",
        "created_by",
        "created",
    )
    list_filter = ("payment_method", "club", "court", "created")
    search_fields = (
        "payment_reference",
        "booking__customer_name",
        "booking__customer_phone",
        "court__name",
        "club__name",
        "club__slug",
        "created_by__username",
    )
    readonly_fields = (
        "club",
        "court",
        "booking",
        "amount",
        "payment_method",
        "payment_reference",
        "created_by",
        "created",
        "modified",
    )
