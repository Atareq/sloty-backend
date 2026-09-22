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
        "total_amount",
        "settled_by",
        "created",
    )
    list_filter = ("club", "court", "settled_by", "created")
    search_fields = (
        "club__slug",
        "club__name",
        "court__name",
        "settled_by__user__phone_number",
    )
    readonly_fields = (
        "club",
        "court",
        "total_amount",
        "settled_by",
        "created",
    )
    inlines = (SettlementTransactionInline,)


@admin.register(SettlementTransaction)
class SettlementTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "settlement", "transaction", "amount", "created")
    list_filter = ("settlement__club", "created")
    search_fields = (
        "settlement__club__slug",
        "settlement__club__name",
        "transaction__payment_reference",
        "transaction__booking__club_player__display_name",
    )
    readonly_fields = ("settlement", "transaction", "amount", "created")
