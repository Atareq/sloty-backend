from decimal import Decimal
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log, settlement_audit_snapshot
from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)
from apps.common.exceptions import SlotyAPIException
from apps.profiles.models import Profile
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction
from apps.transactions.services import settlement_eligible_transactions

ZERO = Decimal("0.00")
NO_UNSETTLED_TRANSACTIONS_MESSAGE = _(
    "There are no unsettled transactions for this collector."
)
DOUBLE_SETTLEMENT_MESSAGE = _(
    "One or more transactions were already settled. Please retry."
)


def _profile_display_name(profile: Optional[Profile]) -> str:
    if profile is None:
        return ""
    return profile.get_display_name()


def calculate(
    club,
    collected_by: Optional[Profile] = None,
    court=None,
    lock=False,
    allow_empty=False,
) -> dict:
    queryset = settlement_eligible_transactions(club=club)
    if collected_by is not None:
        queryset = queryset.filter(created_by=collected_by)
    if lock:
        queryset = queryset.select_for_update(of=("self",))

    candidates = list(queryset.order_by("created", "id"))
    if not candidates and not allow_empty:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_UNSETTLED_TRANSACTIONS",
            message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
        )

    booking_payments = ZERO
    booking_refunds = ZERO
    totals_by_method = {
        str(method): ZERO for method, _ in Transaction.PaymentMethod.choices
    }
    collector_breakdown = {}
    for t in candidates:
        if t.transaction_type == Transaction.Type.PAYMENT:
            booking_payments += t.amount
        else:
            booking_refunds += t.amount
        totals_by_method[str(t.payment_method)] += t.amount

        collector = t.created_by
        collector_id = t.created_by_id
        if collector_id not in collector_breakdown:
            collector_breakdown[collector_id] = {
                "collected_by": collector_id,
                "collected_by_name": _profile_display_name(collector),
                "period_start": t.created,
                "period_end": t.created,
                "transaction_count": 0,
                "total_amount": ZERO,
                "net_amount": ZERO,
                "booking_payments": ZERO,
                "booking_refunds": ZERO,
                "totals_by_payment_method": {
                    str(method): ZERO for method, _ in Transaction.PaymentMethod.choices
                },
            }
        collector_row = collector_breakdown[collector_id]
        collector_row["period_end"] = t.created
        collector_row["transaction_count"] += 1
        collector_row["total_amount"] += t.amount
        collector_row["net_amount"] += t.amount
        if t.transaction_type == Transaction.Type.PAYMENT:
            collector_row["booking_payments"] += t.amount
        else:
            collector_row["booking_refunds"] += t.amount
        collector_row["totals_by_payment_method"][str(t.payment_method)] += t.amount

    total_amount = booking_payments + booking_refunds

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
    collected_by_id = (
        collected_by.id
        if collected_by and hasattr(collected_by, "id")
        else collected_by
    )
    collected_by_name = _profile_display_name(collected_by)

    return {
        "club": club_id,
        "court": court_id,
        "court_name": court_name,
        "collected_by": collected_by_id,
        "collected_by_name": collected_by_name,
        "period_start": candidates[0].created if candidates else None,
        "period_end": candidates[-1].created if candidates else None,
        "transaction_count": len(candidates),
        "total_amount": total_amount,
        "net_amount": total_amount,
        "booking_payments": booking_payments,
        "booking_refunds": booking_refunds,
        "totals_by_payment_method": totals_by_method,
        "transactions": serialized_transactions,
        "collector_breakdown": list(collector_breakdown.values()),
        "_candidates": candidates,
    }


def preview(
    club,
    collected_by: Optional[Profile] = None,
    court=None,
) -> dict:
    calc = calculate(club, collected_by=collected_by, court=court, lock=False)
    calc.pop("_candidates", None)
    return calc


def create(
    club,
    collected_by: Optional[Profile] = None,
    *,
    settled_by: Profile,
    notes="",
    court=None,
) -> Settlement:
    try:
        with transaction.atomic():
            calc = calculate(
                club,
                collected_by=collected_by,
                court=court,
                lock=True,
            )
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
