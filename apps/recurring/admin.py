from django.contrib import admin

from apps.recurring.models import RecurringAgreement, RecurringDepositTransaction


class RecurringDepositTransactionInline(admin.TabularInline):
    model = RecurringDepositTransaction
    extra = 0
    readonly_fields = (
        "transaction_type",
        "amount",
        "payment_method",
        "reference",
        "created_by",
        "created",
    )
    can_delete = False


@admin.register(RecurringAgreement)
class RecurringAgreementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "club",
        "court",
        "customer_name",
        "weekday",
        "start_date",
        "status",
        "deposit_status",
        "deposit_amount",
        "created",
    )
    list_filter = ("status", "deposit_status", "weekday", "club", "court")
    search_fields = ("customer_name", "customer_phone", "club__slug", "court__name")
    readonly_fields = (
        "deposit_amount",
        "deposit_status",
        "refund_notice_days_snapshot",
        "deposit_collected_at",
        "deposit_collected_by",
        "cancellation_requested_at",
        "cancellation_effective_date",
        "cancelled_by",
        "refund_due_at",
        "refunded_at",
        "refunded_by",
        "action_required_code",
        "failed_occurrence_start",
        "action_required_at",
        "created_by",
        "created",
        "modified",
    )
    inlines = (RecurringDepositTransactionInline,)


@admin.register(RecurringDepositTransaction)
class RecurringDepositTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "agreement",
        "transaction_type",
        "amount",
        "payment_method",
        "reference",
        "created_by",
        "created",
    )
    list_filter = ("transaction_type", "payment_method", "club", "court")
    search_fields = ("reference", "agreement__customer_name", "club__slug")
    readonly_fields = (
        "club",
        "court",
        "agreement",
        "transaction_type",
        "amount",
        "payment_method",
        "reference",
        "notes",
        "created_by",
        "created",
        "modified",
    )
