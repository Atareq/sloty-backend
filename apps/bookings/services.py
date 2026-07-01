from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.bookings.models import Booking

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


def blocking_booking_queryset(court, start_time, end_time):
    return Booking.objects.filter(
        court=court,
        status__in=Booking.BLOCKING_STATUSES,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )


def validate_no_booking_overlap(court, start_time, end_time):
    if blocking_booking_queryset(court, start_time, end_time).exists():
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
        from apps.audit.models import AuditLog
        from apps.audit.services import record_audit_log

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


def transition_booking_status(*, access, booking, target_status, actor):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )

        if locked_booking.club_id != access.club.id:
            raise serializers.ValidationError(
                {"booking": "Booking must belong to the selected club."}
            )
        if not access.can_change_booking_status(locked_booking):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You cannot change this booking status.")

        allowed_targets = BOOKING_STATUS_TRANSITIONS.get(locked_booking.status, set())
        if target_status not in allowed_targets:
            raise serializers.ValidationError(
                {
                    "status": (
                        f"Cannot transition booking from "
                        f"{locked_booking.status} to {target_status}."
                    )
                }
            )

        old_status = locked_booking.status
        locked_booking.status = target_status
        locked_booking.save(update_fields=["status", "modified"])
        from apps.audit.models import AuditLog
        from apps.audit.services import record_audit_log

        record_audit_log(
            club=locked_booking.club,
            court=locked_booking.court,
            actor=actor,
            action=getattr(AuditLog.Action, BOOKING_AUDIT_ACTIONS[target_status]),
            entity_type="Booking",
            entity_id=locked_booking.id,
            before_data={"status": old_status},
            after_data={"status": target_status},
        )
        return locked_booking
