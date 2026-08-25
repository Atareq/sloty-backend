from django.contrib import admin

from apps.settlements.models import Settlement, SettlementTransaction


class SettlementTransactionInline(admin.TabularInline):
    model = SettlementTransaction
    extra = 0
    readonly_fields = ("transaction", "amount", "created")
    can_delete = False


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "club",
        "court",
        "status",
        "total_amount",
        "transaction_count",
        "collected_by",
        "created_by",
        "settled_by",
        "settled_at",
        "created",
    )
    list_filter = ("status", "club", "court", "collected_by", "created", "settled_at")
    search_fields = (
        "club__slug",
        "club__name",
        "court__name",
        "collected_by__username",
    )
    readonly_fields = (
        "club",
        "court",
        "status",
        "total_amount",
        "transaction_count",
        "collected_by",
        "created_by",
        "settled_by",
        "settled_at",
        "created",
        "modified",
    )
    inlines = (SettlementTransactionInline,)


@admin.register(SettlementTransaction)
class SettlementTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "settlement", "transaction", "amount", "created")
    list_filter = ("settlement__status", "settlement__club", "created")
    search_fields = (
        "settlement__club__slug",
        "settlement__club__name",
        "transaction__payment_reference",
        "transaction__booking__customer_name",
    )
    readonly_fields = ("settlement", "transaction", "amount", "created")
