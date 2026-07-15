from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.bookings.models import Booking
from apps.courts.models import Court
from apps.transactions.models import Transaction
from apps.transactions.services import get_booking_remaining_amount

BOOKING_STATUS_TRANSITIONS = {
    Booking.Status.HOLD: {
        Booking.Status.CANCELLED,
        Booking.Status.EXPIRED,
    },
    Booking.Status.CONFIRMED: {
        Booking.Status.CANCELLED,
        Booking.Status.COMPLETED,
        Booking.Status.NO_SHOW,
    },
}
BOOKING_AUDIT_ACTIONS = {
    Booking.Status.CANCELLED: "BOOKING_CANCELLED",
    Booking.Status.COMPLETED: "BOOKING_COMPLETED",
    Booking.Status.NO_SHOW: "BOOKING_NO_SHOW",
    Booking.Status.EXPIRED: "BOOKING_EXPIRED",
}
AUTO_COMPLETION_TRANSACTION_NOTE = (
    "Auto cash transaction created on booking completion."
)


def booking_audit_snapshot(booking):
    return {
        "status": booking.status,
        "court_id": booking.court_id,
        "start_time": booking.start_time.isoformat(),
        "end_time": booking.end_time.isoformat(),
        "total_price": str(booking.total_price),
    }


def calculate_booking_price(court, start_time, end_time) -> Decimal:
    duration_minutes = (end_time - start_time).total_seconds() / 60
    slot_duration = court.slot_duration_minutes
    number_of_slots = Decimal(str(duration_minutes / slot_duration))
    return court.default_price * number_of_slots


def validate_booking_duration(court, start_time, end_time):
    if start_time >= end_time:
        raise serializers.ValidationError(
            {"end_time": "end_time must be after start_time."}
        )

    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds <= 0:
        raise serializers.ValidationError(
            {"end_time": "Booking duration must be positive."}
        )

    slot_seconds = court.slot_duration_minutes * 60
    if duration_seconds % slot_seconds != 0:
        raise serializers.ValidationError(
            {
                "end_time": (
                    "Booking duration must be a multiple of the court " "slot duration."
                )
            }
        )


def blocking_booking_queryset(court, start_time, end_time, *, exclude_booking=None):
    queryset = Booking.objects.filter(
        court=court,
        status__in=Booking.BLOCKING_STATUSES,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
    if exclude_booking is not None:
        queryset = queryset.exclude(pk=exclude_booking.pk)
    return queryset


def validate_no_booking_overlap(court, start_time, end_time, *, exclude_booking=None):
    if blocking_booking_queryset(
        court,
        start_time,
        end_time,
        exclude_booking=exclude_booking,
    ).exists():
        raise serializers.ValidationError(
            {"start_time": "This booking overlaps an active booking on this court."}
        )


def create_booking(*, created_by, court, start_time, end_time, **booking_data):
    with transaction.atomic():
        locked_court = court.__class__.objects.select_for_update().get(pk=court.pk)
        validate_booking_duration(locked_court, start_time, end_time)
        validate_no_booking_overlap(locked_court, start_time, end_time)
        total_price = calculate_booking_price(
            locked_court,
            start_time,
            end_time,
        )

        created_booking = Booking.objects.create(
            club=locked_court.club,
            court=locked_court,
            start_time=start_time,
            end_time=end_time,
            total_price=total_price,
            status=Booking.Status.HOLD,
            created_by=created_by,
            **booking_data,
        )
        record_audit_log(
            club=created_booking.club,
            court=created_booking.court,
            actor=created_by,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=created_booking.id,
            after_data=booking_audit_snapshot(created_booking),
        )
        return created_booking


def validate_booking_for_lifecycle_action(*, access, booking):
    if booking.club_id != access.club.id:
        raise serializers.ValidationError(
            {"booking": "Booking must belong to the selected club."}
        )
    if not access.can_change_booking_status(booking):
        raise PermissionDenied("You cannot change this booking status.")


def validate_allowed_status(*, booking, allowed_statuses, action_label):
    if booking.status not in allowed_statuses:
        raise serializers.ValidationError(
            {"status": f"Cannot {action_label} booking from {booking.status}."}
        )


def actor_requires_staff_cancel_reason(access):
    return (
        access.is_staff
        and not access.is_platform_admin
        and not access.is_owner
        and not access.is_manager
    )


def create_lifecycle_audit_log(
    *,
    booking,
    actor,
    action,
    before_data,
    after_data,
    metadata=None,
):
    return record_audit_log(
        club=booking.club,
        court=booking.court,
        actor=actor,
        action=action,
        entity_type="Booking",
        entity_id=booking.id,
        before_data=before_data,
        after_data=after_data,
        metadata=metadata,
    )


def cancel_booking(*, access, booking, actor, reason=""):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        validate_allowed_status(
            booking=locked_booking,
            allowed_statuses={Booking.Status.HOLD, Booking.Status.CONFIRMED},
            action_label="cancel",
        )

        reason = (reason or "").strip()
        if actor_requires_staff_cancel_reason(access) and not reason:
            raise serializers.ValidationError(
                {"reason": "Staff must provide a cancellation reason."}
            )

        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.status = Booking.Status.CANCELLED
        locked_booking.cancelled_at = timezone.now()
        locked_booking.cancellation_reason = reason
        locked_booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancellation_reason",
                "modified",
            ]
        )
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_CANCELLED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {
                "cancelled_at": locked_booking.cancelled_at.isoformat(),
                "cancellation_reason": locked_booking.cancellation_reason,
            },
            metadata={"reason": reason} if reason else {},
        )
        return locked_booking


def no_show_booking(*, access, booking, actor, reason=""):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        validate_allowed_status(
            booking=locked_booking,
            allowed_statuses={Booking.Status.CONFIRMED},
            action_label="mark no-show",
        )

        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.status = Booking.Status.NO_SHOW
        locked_booking.no_show_at = timezone.now()
        locked_booking.no_show_reason = (reason or "").strip()
        locked_booking.save(
            update_fields=["status", "no_show_at", "no_show_reason", "modified"]
        )
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_NO_SHOW,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {
                "no_show_at": locked_booking.no_show_at.isoformat(),
                "no_show_reason": locked_booking.no_show_reason,
            },
            metadata=(
                {"reason": locked_booking.no_show_reason}
                if locked_booking.no_show_reason
                else {}
            ),
        )
        return locked_booking


def reschedule_booking(
    *,
    access,
    booking,
    actor,
    court,
    start_time,
    end_time,
    reason="",
):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        validate_allowed_status(
            booking=locked_booking,
            allowed_statuses={Booking.Status.HOLD, Booking.Status.CONFIRMED},
            action_label="reschedule",
        )

        locked_court = (
            Court.objects.select_for_update().select_related("club").get(pk=court.pk)
        )
        if locked_court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if not access.can_access_court(locked_court):
            raise PermissionDenied("You cannot reschedule bookings to this court.")

        validate_booking_duration(locked_court, start_time, end_time)
        validate_no_booking_overlap(
            locked_court,
            start_time,
            end_time,
            exclude_booking=locked_booking,
        )

        before_data = booking_audit_snapshot(locked_booking)
        new_price = calculate_booking_price(locked_court, start_time, end_time)
        locked_booking.court = locked_court
        locked_booking.start_time = start_time
        locked_booking.end_time = end_time
        if new_price > locked_booking.total_price:
            locked_booking.total_price = new_price
        locked_booking.reschedule_reason = (reason or "").strip()
        locked_booking.save(
            update_fields=[
                "court",
                "start_time",
                "end_time",
                "total_price",
                "reschedule_reason",
                "modified",
            ]
        )
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_RESCHEDULED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {"reschedule_reason": locked_booking.reschedule_reason},
            metadata=(
                {"reason": locked_booking.reschedule_reason}
                if locked_booking.reschedule_reason
                else {}
            ),
        )
        return locked_booking


def get_remaining_amount(booking) -> Decimal:
    return get_booking_remaining_amount(booking)


def record_transaction_created_audit(*, transaction_obj, actor):
    return record_audit_log(
        club=transaction_obj.club,
        court=transaction_obj.court,
        actor=actor,
        action=AuditLog.Action.TRANSACTION_CREATED,
        entity_type="Transaction",
        entity_id=transaction_obj.id,
        after_data={
            "transaction_id": transaction_obj.id,
            "booking_id": transaction_obj.booking_id,
            "court_id": transaction_obj.court_id,
            "amount": str(transaction_obj.amount),
            "payment_method": transaction_obj.payment_method,
            "payment_reference": transaction_obj.payment_reference,
        },
        metadata={"source": "booking_completion_auto_cash"},
    )


def complete_booking(
    *,
    access,
    booking,
    actor,
    confirm_collect_remaining_cash=False,
):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        validate_allowed_status(
            booking=locked_booking,
            allowed_statuses={Booking.Status.CONFIRMED},
            action_label="complete",
        )

        remaining_amount = get_remaining_amount(locked_booking)
        if remaining_amount > 0 and not confirm_collect_remaining_cash:
            raise serializers.ValidationError(
                {
                    "confirm_collect_remaining_cash": (
                        "Remaining amount must be confirmed as collected before "
                        "completing this booking."
                    )
                }
            )

        created_transaction = None
        if remaining_amount > 0:
            created_transaction = Transaction.objects.create(
                booking=locked_booking,
                club=locked_booking.club,
                court=locked_booking.court,
                amount=remaining_amount,
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference="",
                notes=AUTO_COMPLETION_TRANSACTION_NOTE,
                created_by=actor,
            )
            record_transaction_created_audit(
                transaction_obj=created_transaction,
                actor=actor,
            )

        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.status = Booking.Status.COMPLETED
        locked_booking.completed_at = timezone.now()
        locked_booking.save(update_fields=["status", "completed_at", "modified"])
        metadata = {}
        if created_transaction is not None:
            metadata = {
                "auto_cash_transaction_id": created_transaction.id,
                "remaining_amount_collected": str(remaining_amount),
            }
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_COMPLETED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {"completed_at": locked_booking.completed_at.isoformat()},
            metadata=metadata,
        )
        return locked_booking


def expire_booking(*, access, booking, actor):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        validate_allowed_status(
            booking=locked_booking,
            allowed_statuses={Booking.Status.HOLD},
            action_label="expire",
        )
        return expire_locked_booking(locked_booking=locked_booking, actor=actor)


def expire_locked_booking(*, locked_booking, actor, metadata=None):
    before_data = booking_audit_snapshot(locked_booking)
    locked_booking.status = Booking.Status.EXPIRED
    locked_booking.expired_at = timezone.now()
    locked_booking.save(update_fields=["status", "expired_at", "modified"])
    create_lifecycle_audit_log(
        booking=locked_booking,
        actor=actor,
        action=AuditLog.Action.BOOKING_EXPIRED,
        before_data=before_data,
        after_data=booking_audit_snapshot(locked_booking)
        | {"expired_at": locked_booking.expired_at.isoformat()},
        metadata=metadata or {},
    )
    return locked_booking


def expire_due_hold_bookings(*, now=None):
    now = now or timezone.now()
    due_ids = []
    for booking in (
        Booking.objects.filter(status=Booking.Status.HOLD)
        .select_related("court")
        .only("id", "created", "status", "court__internal_hold_expiry_hours")
    ):
        expiry_cutoff = now - timedelta(hours=booking.court.internal_hold_expiry_hours)
        if booking.created <= expiry_cutoff:
            due_ids.append(booking.id)

    expired_bookings = []
    with transaction.atomic():
        locked_bookings = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .filter(id__in=due_ids, status=Booking.Status.HOLD)
            .order_by("id")
        )
        for locked_booking in locked_bookings:
            expiry_cutoff = now - timedelta(
                hours=locked_booking.court.internal_hold_expiry_hours
            )
            if locked_booking.created > expiry_cutoff:
                continue
            expired_bookings.append(
                expire_locked_booking(
                    locked_booking=locked_booking,
                    actor=None,
                    metadata={"source": "automatic_hold_expiry"},
                )
            )
    return expired_bookings
