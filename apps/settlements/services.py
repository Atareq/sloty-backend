from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

NO_UNSETTLED_TRANSACTIONS_MESSAGE = (
    "No unsettled transactions found for the selected period."
)
DOUBLE_SETTLEMENT_MESSAGE = (
    "One or more transactions were already settled. Please retry."
)


def validate_period(period_start, period_end):
    if period_start >= period_end:
        raise serializers.ValidationError(
            {"period_end": "period_end must be after period_start."}
        )


def validate_settlement_access(*, access, court=None):
    if court is not None and court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Court must belong to the selected club."}
        )
    if not access.can_create_settlement(court):
        raise PermissionDenied("You cannot manage settlements for this club.")


def unsettled_transactions_queryset(*, access, period_start, period_end, court=None):
    queryset = Transaction.objects.filter(
        club=access.club,
        created__gte=period_start,
        created__lt=period_end,
        settlement_line__isnull=True,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset.select_related("booking", "club", "court", "created_by").order_by(
        "created", "id"
    )


def build_settlement_summary(*, access, period_start, period_end, court, queryset):
    aggregate = queryset.aggregate(total=Sum("amount"))
    return {
        "club": access.club.id,
        "court": court.id if court else None,
        "period_start": period_start,
        "period_end": period_end,
        "transaction_count": queryset.count(),
        "total_amount": aggregate["total"] or Decimal("0.00"),
    }


def preview_settlement(*, access, period_start, period_end, court=None):
    validate_period(period_start, period_end)
    validate_settlement_access(access=access, court=court)
    queryset = unsettled_transactions_queryset(
        access=access,
        period_start=period_start,
        period_end=period_end,
        court=court,
    )
    return build_settlement_summary(
        access=access,
        period_start=period_start,
        period_end=period_end,
        court=court,
        queryset=queryset,
    )


def create_settlement(
    *,
    access,
    period_start,
    period_end,
    court=None,
    notes="",
    created_by,
):
    try:
        with transaction.atomic():
            validate_period(period_start, period_end)
            validate_settlement_access(access=access, court=court)
            candidates = list(
                unsettled_transactions_queryset(
                    access=access,
                    period_start=period_start,
                    period_end=period_end,
                    court=court,
                )
                .select_for_update()
                .order_by("id")
            )
            if not candidates:
                raise serializers.ValidationError(
                    {"transactions": NO_UNSETTLED_TRANSACTIONS_MESSAGE}
                )

            total_amount = sum(
                (transaction_obj.amount for transaction_obj in candidates),
                Decimal("0.00"),
            )
            created_settlement = Settlement.objects.create(
                club=access.club,
                court=court,
                period_start=period_start,
                period_end=period_end,
                status=Settlement.Status.PENDING,
                total_amount=total_amount,
                transaction_count=len(candidates),
                notes=notes,
                created_by=created_by,
            )
            SettlementTransaction.objects.bulk_create(
                [
                    SettlementTransaction(
                        settlement=created_settlement,
                        transaction=transaction_obj,
                        amount=transaction_obj.amount,
                    )
                    for transaction_obj in candidates
                ]
            )
            return created_settlement
    except IntegrityError as exc:
        raise serializers.ValidationError(
            {"transactions": DOUBLE_SETTLEMENT_MESSAGE}
        ) from exc


def mark_settlement_settled(*, access, settlement, actor):
    with transaction.atomic():
        locked_settlement = (
            Settlement.objects.select_for_update()
            .select_related("club", "court", "created_by", "settled_by")
            .get(pk=settlement.pk)
        )
        if not access.can_access_settlement(locked_settlement):
            raise PermissionDenied("You cannot access this settlement.")
        if not access.can_manage_settlements():
            raise PermissionDenied("You cannot manage settlements for this club.")
        if locked_settlement.status != Settlement.Status.PENDING:
            raise serializers.ValidationError(
                {"status": "Only pending settlements can be marked as settled."}
            )

        locked_settlement.status = Settlement.Status.SETTLED
        locked_settlement.settled_by = actor
        locked_settlement.settled_at = timezone.now()
        locked_settlement.save(
            update_fields=["status", "settled_by", "settled_at", "modified"]
        )
        return locked_settlement
