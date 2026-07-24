from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value, prefetch_related_objects
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.bookings.models import Booking
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.courts.pricing import (
    calculate_booking_price_from_schedule,
    slot_price_from_schedule,
)
from apps.transactions.services import get_booking_remaining_amount

FREE_SLOT_STATUS = "FREE"
UNAVAILABLE_SLOT_STATUS = "UNAVAILABLE"
MAX_SLOT_PERIOD_DAYS = 31
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
BOOKING_SLOT_UNAVAILABLE_MESSAGE = _("The selected booking slot is not available.")
BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT_MESSAGE = _(
    "This booking cannot be completed until the remaining amount is paid."
)
COURT_CLOSED_ON_THIS_DAY_MESSAGE = _("The court is closed on this day.")
PRICING_NOT_CONFIGURED_LABEL = _("Pricing not configured")
BOOKING_NOT_IN_CLUB_MESSAGE = _("Booking must belong to the selected club.")
BOOKING_ALREADY_CANCELLED_MESSAGE = _("This booking is already cancelled.")
INVALID_BOOKING_STATUS_TRANSITION_MESSAGE = _(
    "This booking status transition is not allowed."
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
    return calculate_booking_price_from_schedule(
        court=court,
        start_time=start_time,
        end_time=end_time,
    )


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
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_SLOT_UNAVAILABLE",
            message=BOOKING_SLOT_UNAVAILABLE_MESSAGE,
        )


def local_datetime_for_date(value, value_time):
    naive_value = datetime.combine(value, value_time)
    if timezone.is_naive(naive_value):
        return timezone.make_aware(naive_value, timezone.get_current_timezone())
    return naive_value


def format_slot_label(slot_status):
    if slot_status == FREE_SLOT_STATUS:
        return str(_("Available"))
    if slot_status == UNAVAILABLE_SLOT_STATUS:
        return str(PRICING_NOT_CONFIGURED_LABEL)
    return str(Booking.Status(slot_status).label)


def booking_slot_payload(booking):
    paid_amount = getattr(booking, "paid_amount", Decimal("0.00")) or Decimal("0.00")
    remaining_amount = max(booking.total_price - paid_amount, Decimal("0.00"))
    return {
        "id": booking.id,
        "status": booking.status,
        "status_label": str(booking.get_status_display()),
        "customer_name": booking.customer_name,
        "total_booking_value": f"{booking.total_price:.2f}",
        "total_paid_amount": f"{paid_amount:.2f}",
        "remaining_amount": f"{remaining_amount:.2f}",
    }


def booking_overlaps_slot(booking, slot_start, slot_end):
    return booking.start_time < slot_end and booking.end_time > slot_start


def generate_booking_slots(*, access, court, date_from, date_to):
    if court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Court must belong to the selected club."}
        )
    if not access.can_view_court_availability(court):
        raise PermissionDenied("You cannot view availability for this court.")
    if not court.is_active:
        raise serializers.ValidationError({"court": "Court is inactive."})

    range_start = local_datetime_for_date(date_from, time.min)
    range_end = local_datetime_for_date(date_to + timedelta(days=1), time.min)
    if "working_hours" not in getattr(court, "_prefetched_objects_cache", {}):
        prefetch_related_objects([court], "working_hours__pricing_periods")
    working_hours = list(court.working_hours.all())
    working_hours_by_weekday = {row.weekday: row for row in working_hours}
    blocking_bookings = list(
        Booking.objects.filter(
            club=access.club,
            court=court,
            status__in=Booking.BLOCKING_STATUSES,
            start_time__lt=range_end,
            end_time__gt=range_start,
        )
        .annotate(
            paid_amount=Coalesce(
                Sum(
                    "transactions__amount",
                    filter=Q(transactions__is_cancelled=False),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .order_by("start_time", "id")
    )

    slots = []
    current_date = date_from
    has_closed_day = False
    while current_date <= date_to:
        working_hour = working_hours_by_weekday.get(current_date.weekday())
        if (
            working_hour is None
            or working_hour.is_closed
            or working_hour.opens_at is None
            or working_hour.closes_at is None
        ):
            has_closed_day = True
            current_date += timedelta(days=1)
            continue

        day_open = local_datetime_for_date(current_date, working_hour.opens_at)
        day_close = local_datetime_for_date(current_date, working_hour.closes_at)
        slot_delta = timedelta(minutes=court.slot_duration_minutes)
        slot_start = day_open
        while slot_start + slot_delta <= day_close:
            slot_end = slot_start + slot_delta
            booking = next(
                (
                    candidate
                    for candidate in blocking_bookings
                    if booking_overlaps_slot(candidate, slot_start, slot_end)
                ),
                None,
            )
            slot_price = slot_price_from_schedule(
                court=court,
                start_time=slot_start,
                end_time=slot_end,
                working_hours=working_hours,
            )
            if booking is not None:
                slot_status = booking.status
                is_available = False
            elif slot_price is None:
                slot_status = UNAVAILABLE_SLOT_STATUS
                is_available = False
            else:
                slot_status = FREE_SLOT_STATUS
                is_available = True
            slots.append(
                {
                    "date": current_date.isoformat(),
                    "start_time": slot_start,
                    "end_time": slot_end,
                    "slot_price": (
                        f"{slot_price:.2f}" if slot_price is not None else None
                    ),
                    "slot_status": slot_status,
                    "is_available": is_available,
                    "booking": (
                        booking_slot_payload(booking) if booking is not None else None
                    ),
                    "label": format_slot_label(slot_status),
                }
            )
            slot_start = slot_end

        current_date += timedelta(days=1)

    response = {
        "court": court.id,
        "court_name": court.name,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "slot_duration_minutes": court.slot_duration_minutes,
        "slots": slots,
    }
    if not slots and has_closed_day:
        response["message"] = str(COURT_CLOSED_ON_THIS_DAY_MESSAGE)
    return response


def create_booking(*, created_by, court, start_time, end_time, **booking_data):
    with transaction.atomic():
        locked_court = (
            court.__class__.objects.select_for_update()
            .prefetch_related("working_hours__pricing_periods")
            .get(pk=court.pk)
        )
        validate_booking_duration(locked_court, start_time, end_time)
        total_price = calculate_booking_price(
            locked_court,
            start_time,
            end_time,
        )
        validate_no_booking_overlap(locked_court, start_time, end_time)

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
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_NOT_IN_CLUB",
            message=BOOKING_NOT_IN_CLUB_MESSAGE,
        )
    if not access.can_change_booking_status(booking):
        raise PermissionDenied("You cannot change this booking status.")


def validate_allowed_status(*, booking, allowed_statuses, action_label):
    if booking.status not in allowed_statuses:
        code = "INVALID_BOOKING_STATUS_TRANSITION"
        message = INVALID_BOOKING_STATUS_TRANSITION_MESSAGE
        if action_label == "cancel" and booking.status == Booking.Status.CANCELLED:
            code = "BOOKING_ALREADY_CANCELLED"
            message = BOOKING_ALREADY_CANCELLED_MESSAGE
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
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
            Court.objects.select_for_update()
            .select_related("club")
            .prefetch_related("working_hours__pricing_periods")
            .get(pk=court.pk)
        )
        if locked_court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if not access.can_access_court(locked_court):
            raise PermissionDenied("You cannot reschedule bookings to this court.")

        validate_booking_duration(locked_court, start_time, end_time)
        new_price = calculate_booking_price(locked_court, start_time, end_time)
        validate_no_booking_overlap(
            locked_court,
            start_time,
            end_time,
            exclude_booking=locked_booking,
        )

        before_data = booking_audit_snapshot(locked_booking)
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


def ensure_booking_can_be_completed(booking):
    remaining_amount = get_remaining_amount(booking)
    if remaining_amount > 0:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT",
            message=BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT_MESSAGE,
            details={
                "booking_id": booking.id,
                "remaining_amount": f"{remaining_amount:.2f}",
            },
        )
    return remaining_amount


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

        ensure_booking_can_be_completed(locked_booking)

        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.status = Booking.Status.COMPLETED
        locked_booking.completed_at = timezone.now()
        locked_booking.save(update_fields=["status", "completed_at", "modified"])
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_COMPLETED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {"completed_at": locked_booking.completed_at.isoformat()},
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
