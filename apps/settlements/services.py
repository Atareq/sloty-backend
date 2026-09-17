from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.audit.services import record_audit_log, settlement_audit_snapshot
from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)
from apps.common.exceptions import SlotyAPIException
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

ZERO = Decimal("0.00")
NO_UNSETTLED_TRANSACTIONS_MESSAGE = _(
    "There are no unsettled transactions for this user."
)
DOUBLE_SETTLEMENT_MESSAGE = _(
    "One or more transactions were already settled. Please retry."
)


def _user_display_name(user) -> str:
    if user is None:
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _calculate(club, user=None, court=None, lock=False) -> dict:
    queryset = Transaction.objects.filter(
        club=club,
        settlement_line__isnull=True,
        is_cancelled=False,
    )
    if user is not None:
        queryset = queryset.filter(created_by=user)
    if lock:
        queryset = queryset.select_for_update(of=("self",))

    candidates = list(
        queryset.select_related(
            "booking",
            "booking__club_player__player_profile",
            "court",
            "created_by",
        ).order_by("created", "id")
    )
    if not candidates:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_UNSETTLED_TRANSACTIONS",
            message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
        )

    booking_payments = sum(
        (
            t.amount
            for t in candidates
            if t.transaction_type == Transaction.Type.PAYMENT
        ),
        ZERO,
    )
    booking_refunds = sum(
        (t.amount for t in candidates if t.transaction_type == Transaction.Type.REFUND),
        ZERO,
    )
    total_amount = booking_payments + booking_refunds

    totals_by_method = {
        str(method): ZERO for method, _ in Transaction.PaymentMethod.choices
    }
    for t in candidates:
        totals_by_method[str(t.payment_method)] += t.amount

    serialized_transactions = [
        {
            "id": t.id,
            "kind": t.transaction_type,
            "booking": t.booking_id,
            "booking_customer_name": booking_customer_display_name(t.booking),
            "booking_customer_phone": booking_customer_display_phone(t.booking),
            "booking_start_time": t.booking.start_time if t.booking else None,
            "booking_end_time": t.booking.end_time if t.booking else None,
            "court": t.court_id,
            "court_name": t.court.name if t.court else "",
            "amount": t.amount,
            "payment_method": t.payment_method,
            "payment_reference": t.payment_reference,
            "created": t.created,
        }
        for t in candidates
    ]

    club_id = club.id if hasattr(club, "id") else club
    court_id = court.id if court and hasattr(court, "id") else court
    court_name = court.name if court and hasattr(court, "name") else ""
    user_id = user.id if user and hasattr(user, "id") else user
    user_name = _user_display_name(user)

    return {
        "club": club_id,
        "court": court_id,
        "court_name": court_name,
        "collected_by": user_id,
        "collected_by_name": user_name,
        "user": user_id,
        "user_name": user_name,
        "period_start": candidates[0].created,
        "period_end": candidates[-1].created,
        "transaction_count": len(candidates),
        "total_amount": total_amount,
        "net_amount": total_amount,
        "booking_payments": booking_payments,
        "booking_refunds": booking_refunds,
        "totals_by_payment_method": totals_by_method,
        "transactions": serialized_transactions,
        "_candidates": candidates,
    }


def preview(club, user=None, court=None) -> dict:
    calc = _calculate(club, user=user, court=court, lock=False)
    calc.pop("_candidates", None)
    return calc


def create(club, user=None, *, settled_by, notes="", court=None) -> Settlement:
    try:
        with transaction.atomic():
            calc = _calculate(club, user=user, court=court, lock=True)
            settlement = Settlement.objects.create(
                club=club,
                court=court,
                total_amount=calc["total_amount"],
                notes=notes,
                settled_by=settled_by,
            )
            SettlementTransaction.objects.bulk_create(
                [
                    SettlementTransaction(
                        settlement=settlement,
                        transaction=t,
                        amount=t.amount,
                    )
                    for t in calc["_candidates"]
                ]
            )

            from apps.audit.models import AuditLog

            record_audit_log(
                club=settlement.club,
                court=settlement.court,
                actor=settled_by,
                action=AuditLog.Action.SETTLEMENT_CREATED,
                entity_type="Settlement",
                entity_id=settlement.id,
                after_data=settlement_audit_snapshot(settlement)
                | {
                    "booking_payments": str(calc["booking_payments"]),
                    "booking_refunds": str(calc["booking_refunds"]),
                    "transaction_ids": [t.id for t in calc["_candidates"]],
                },
            )
            return settlement
    except IntegrityError as exc:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="SETTLEMENT_CONFLICT",
            message=DOUBLE_SETTLEMENT_MESSAGE,
        ) from exc
