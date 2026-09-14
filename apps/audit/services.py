from apps.audit.models import AuditLog
from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)


def user_display_name(user):
    if user is None:
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def booking_audit_snapshot(booking):
    return {
        "booking_id": booking.id,
        "customer_name": booking_customer_display_name(booking),
        "customer_phone": booking_customer_display_phone(booking),
        "status": booking.status,
        "source": booking.source,
        "recurrence_status": booking.recurrence_status,
        "previous_recurring_booking_id": booking.previous_recurring_booking_id,
        "court_id": booking.court_id,
        "court_name": booking.court.name if booking.court_id else "",
        "start_time": booking.start_time.isoformat(),
        "end_time": booking.end_time.isoformat(),
        "total_price": str(booking.total_price),
    }


def transaction_audit_snapshot(transaction_obj):
    booking = transaction_obj.booking
    court = transaction_obj.court
    collector = transaction_obj.created_by
    return {
        "transaction_id": transaction_obj.id,
        "transaction_type": transaction_obj.transaction_type,
        "booking_id": transaction_obj.booking_id,
        "customer_name": booking_customer_display_name(booking),
        "amount": str(transaction_obj.amount),
        "client_request_id": (
            str(transaction_obj.client_request_id)
            if transaction_obj.client_request_id
            else None
        ),
        "payment_method": transaction_obj.payment_method,
        "payment_reference": transaction_obj.payment_reference,
        "occurred_at": transaction_obj.occurred_at.isoformat(),
        "collector_id": transaction_obj.created_by_id,
        "collector_name": user_display_name(collector),
        "court_id": transaction_obj.court_id,
        "court_name": court.name,
    }


def settlement_audit_snapshot(settlement):
    return {
        "settlement_id": settlement.id,
        "court_id": settlement.court_id,
        "court_name": settlement.court.name if settlement.court_id else "",
        "collected_by_id": settlement.collected_by_id,
        "collected_by_name": user_display_name(settlement.collected_by),
        "period_start": settlement.period_start.isoformat(),
        "period_end": settlement.period_end.isoformat(),
        "status": settlement.status,
        "settled_by_id": settlement.settled_by_id,
        "settled_by_name": user_display_name(settlement.settled_by),
        "settled_at": (
            settlement.settled_at.isoformat() if settlement.settled_at else None
        ),
        "total_amount": str(settlement.total_amount),
        "transaction_count": settlement.transaction_count,
    }


def record_audit_log(
    *,
    club,
    actor,
    action,
    entity_type,
    entity_id,
    court=None,
    before_data=None,
    after_data=None,
    metadata=None,
):
    metadata = dict(metadata or {})
    display_snapshot = dict(metadata.get("display_snapshot") or {})
    display_snapshot.setdefault("actor_name", user_display_name(actor))
    display_snapshot.setdefault("court_name", court.name if court is not None else "")
    metadata["display_snapshot"] = display_snapshot
    return AuditLog.objects.create(
        club=club,
        court=court,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_data=before_data or {},
        after_data=after_data or {},
        metadata=metadata,
    )
