from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import (
    booking_audit_snapshot,
    record_audit_log,
    transaction_audit_snapshot,
)
from apps.bookings.filters import (
    annotate_booking_hold_expires_at,
    compute_booking_hold_expires_at,
)
from apps.bookings.models import Booking, BookingAttempt
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.courts.pricing import (
    BOOKING_MULTIDAY_NOT_SUPPORTED_MESSAGE,
    BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE,
    BOOKING_PRICE_NOT_CONFIGURED_MESSAGE,
    BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE,
    calculate_booking_price_from_schedule,
    slot_price_from_schedule,
    working_hour_bounds,
)
from apps.transactions.models import Transaction
from apps.transactions.services import (
    annotate_booking_paid_amount,
    get_booking_paid_amount,
    get_booking_refunded_amount,
    get_booking_remaining_amount,
    normalize_payment_reference,
    validate_duplicate_payment_reference,
)

FREE_SLOT_STATUS = "FREE"
UNAVAILABLE_SLOT_STATUS = "UNAVAILABLE"
RECURRING_RESERVED_SLOT_STATUS = "RECURRING_RESERVED"
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
BOOKING_SLOT_UNAVAILABLE_MESSAGE = _("The selected booking slot is not available.")
RECURRING_UNAVAILABLE_MESSAGE = _(
    "The selected recurring booking pattern is not available."
)
BOOKING_CLIENT_REQUEST_MISMATCH_MESSAGE = _(
    "This client request id was already used for a different booking request."
)
BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT_MESSAGE = _(
    "This booking cannot be completed until the remaining amount is paid."
)
PREVIOUS_BOOKING_ATTEMPT_REJECTED_MESSAGE = _(
    "This booking request was previously rejected."
)
COURT_CLOSED_ON_THIS_DAY_MESSAGE = _("The court is closed on this day.")
PRICING_NOT_CONFIGURED_LABEL = _("Pricing not configured")
BOOKING_NOT_IN_CLUB_MESSAGE = _("Booking must belong to the selected club.")
BOOKING_ALREADY_CANCELLED_MESSAGE = _("This booking is already cancelled.")
INVALID_BOOKING_STATUS_TRANSITION_MESSAGE = _(
    "This booking status transition is not allowed."
)
BOOKING_CANCELLATION_TIME_PASSED_MESSAGE = _(
    "A booking cannot be cancelled after its start time."
)
BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED_MESSAGE = _(
    "Booking cancellation policy is not configured for this court."
)
REFUND_PAYMENT_METHOD_REQUIRED_MESSAGE = _("Refund payment method is required.")
REFUND_REFERENCE_REQUIRED_MESSAGE = _("Payment reference is required for this court.")
RECURRENCE_CONTINUATION_DECISION_REQUIRED_MESSAGE = _(
    "Choose whether this recurring booking should continue."
)
BOOKING_RECURRENCE_NOT_ACTIVE_MESSAGE = _("This booking has no active recurrence.")
BOOKING_ATTEMPT_CANNOT_BE_DISMISSED_MESSAGE = _(
    "Only rejected booking attempts without a resolved booking can be dismissed."
)
RECURRING_BOOKING_RESCHEDULE_NOT_SUPPORTED_MESSAGE = _(
    "Active recurring bookings cannot be rescheduled."
)
RECURRENCE_CANNOT_CONTINUE_MESSAGE = _("This recurrence cannot be continued.")
NEXT_RECURRING_SLOT_UNAVAILABLE_MESSAGE = _("The next recurring slot is not available.")
NEXT_OCCURRENCE_PLAN_ERROR_CODES = {
    "BOOKING_SLOT_UNAVAILABLE",
    "BOOKING_OUTSIDE_WORKING_HOURS",
    "BOOKING_PRICE_NOT_CONFIGURED",
    "BOOKING_MULTIDAY_NOT_SUPPORTED",
    "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID",
}
BOOKING_ATTEMPT_FAILURE_MESSAGES = {
    "BOOKING_SLOT_UNAVAILABLE": BOOKING_SLOT_UNAVAILABLE_MESSAGE,
    "RECURRING_UNAVAILABLE": RECURRING_UNAVAILABLE_MESSAGE,
    "BOOKING_OUTSIDE_WORKING_HOURS": BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE,
    "BOOKING_PRICE_NOT_CONFIGURED": BOOKING_PRICE_NOT_CONFIGURED_MESSAGE,
    "BOOKING_MULTIDAY_NOT_SUPPORTED": BOOKING_MULTIDAY_NOT_SUPPORTED_MESSAGE,
    "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID": (
        BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE
    ),
    "VALIDATION_ERROR": _("The submitted data was invalid."),
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


def localized_interval(start_time, end_time):
    return timezone.localtime(start_time), timezone.localtime(end_time)


def weekly_pattern_matches(*, anchor_booking, candidate_start, candidate_end):
    if (
        anchor_booking.source != Booking.Source.RECURRING
        or anchor_booking.recurrence_status != Booking.RecurrenceStatus.ACTIVE
        or candidate_end <= anchor_booking.start_time
    ):
        return False

    anchor_start, anchor_end = localized_interval(
        anchor_booking.start_time,
        anchor_booking.end_time,
    )
    candidate_local_start, candidate_local_end = localized_interval(
        candidate_start,
        candidate_end,
    )
    day_delta = candidate_local_start.date() - anchor_start.date()
    if day_delta.days < 0 or day_delta.days % 7 != 0:
        return False

    projected_start = anchor_start + timedelta(days=day_delta.days)
    projected_end = anchor_end + timedelta(days=day_delta.days)
    return (
        projected_start < candidate_local_end and projected_end > candidate_local_start
    )


def active_recurring_anchor_queryset(court, *, exclude_booking=None):
    queryset = Booking.objects.filter(
        court=court,
        source=Booking.Source.RECURRING,
        recurrence_status=Booking.RecurrenceStatus.ACTIVE,
    ).select_related("club", "court")
    if exclude_booking is not None:
        queryset = queryset.exclude(pk=exclude_booking.pk)
    return queryset.order_by("start_time", "id")


def find_active_recurrence_conflict(
    *,
    court,
    start_time,
    end_time,
    exclude_booking=None,
    anchors=None,
):
    candidates = anchors
    if candidates is None:
        candidates = list(
            active_recurring_anchor_queryset(court, exclude_booking=exclude_booking)
        )
    for anchor in candidates:
        if exclude_booking is not None and anchor.pk == exclude_booking.pk:
            continue
        if weekly_pattern_matches(
            anchor_booking=anchor,
            candidate_start=start_time,
            candidate_end=end_time,
        ):
            return anchor
    return None


def recurrence_conflict_details(*, conflict_type, start_time, booking=None):
    details = {
        "conflict_type": conflict_type,
        "first_conflict_start": start_time.isoformat() if start_time else None,
    }
    if booking is not None:
        details["conflicting_booking_id"] = booking.id
    return details


def raise_slot_unavailable(*, details=None):
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code="BOOKING_SLOT_UNAVAILABLE",
        message=BOOKING_SLOT_UNAVAILABLE_MESSAGE,
        details=details or {},
    )


def raise_recurring_unavailable(*, details=None):
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code="RECURRING_UNAVAILABLE",
        message=RECURRING_UNAVAILABLE_MESSAGE,
        details=details or {},
    )


def booking_matches_client_request(
    booking,
    *,
    court,
    start_time,
    end_time,
    booking_data,
):
    expected_source = booking_data.get("source", Booking.Source.MANUAL)
    expected_notes = booking_data.get("notes", "") or ""
    expected_phone = booking_data.get("customer_phone")

    return (
        booking.court_id == court.id
        and booking.start_time == start_time
        and booking.end_time == end_time
        and booking.source == expected_source
        and booking.customer_name == booking_data.get("customer_name")
        and str(booking.customer_phone) == str(expected_phone)
        and (booking.notes or "") == expected_notes
    )


def resolve_idempotent_booking_request(
    *,
    club,
    court,
    start_time,
    end_time,
    booking_data,
):
    client_request_id = booking_data.get("client_request_id")
    if client_request_id is None:
        return None

    existing_booking = (
        Booking.objects.select_for_update()
        .filter(
            club=club,
            client_request_id=client_request_id,
        )
        .select_related("club", "court")
        .first()
    )
    if existing_booking is None:
        return None

    if booking_matches_client_request(
        existing_booking,
        court=court,
        start_time=start_time,
        end_time=end_time,
        booking_data=booking_data,
    ):
        existing_booking._sloty_idempotency_reused = True
        return existing_booking

    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code="BOOKING_CLIENT_REQUEST_MISMATCH",
        message=BOOKING_CLIENT_REQUEST_MISMATCH_MESSAGE,
        details={
            "client_request_id": str(client_request_id),
            "existing_booking_id": existing_booking.id,
        },
    )


def requested_recurring_from_booking_data(booking_data):
    return booking_data.get("source") == Booking.Source.RECURRING


def requested_source_from_booking_data(booking_data):
    return booking_data.get("source", Booking.Source.MANUAL)


def booking_attempt_matches_client_request(
    attempt,
    *,
    court,
    start_time,
    end_time,
    booking_data,
):
    expected_notes = booking_data.get("notes", "") or ""
    expected_phone = booking_data.get("customer_phone")

    return (
        attempt.court_id == court.id
        and attempt.requested_start == start_time
        and attempt.requested_end == end_time
        and attempt.customer_name == booking_data.get("customer_name")
        and str(attempt.customer_phone) == str(expected_phone)
        and (attempt.notes or "") == expected_notes
        and attempt.requested_source == requested_source_from_booking_data(booking_data)
        and attempt.requested_recurring
        == requested_recurring_from_booking_data(booking_data)
    )


def raise_booking_attempt_mismatch(*, client_request_id, attempt):
    details = {"client_request_id": str(client_request_id)}
    if attempt.booking_id is not None:
        details["existing_booking_id"] = attempt.booking_id
    else:
        details["existing_attempt_id"] = attempt.id
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code="BOOKING_CLIENT_REQUEST_MISMATCH",
        message=BOOKING_CLIENT_REQUEST_MISMATCH_MESSAGE,
        details=details,
    )


def raise_previous_booking_attempt_rejection(attempt):
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code=attempt.failure_code,
        message=BOOKING_ATTEMPT_FAILURE_MESSAGES.get(
            attempt.failure_code,
            PREVIOUS_BOOKING_ATTEMPT_REJECTED_MESSAGE,
        ),
        details=attempt.failure_details or {},
    )


def resolve_idempotent_booking_attempt(
    *,
    club,
    court,
    start_time,
    end_time,
    booking_data,
):
    client_request_id = booking_data.get("client_request_id")
    if client_request_id is None:
        return None

    attempt = (
        BookingAttempt.objects.select_for_update(of=("self",))
        .filter(club=club, client_request_id=client_request_id)
        .select_related("booking", "court", "club")
        .first()
    )
    if attempt is None:
        return None

    if not booking_attempt_matches_client_request(
        attempt,
        court=court,
        start_time=start_time,
        end_time=end_time,
        booking_data=booking_data,
    ):
        raise_booking_attempt_mismatch(
            client_request_id=client_request_id,
            attempt=attempt,
        )
    if attempt.outcome == BookingAttempt.Outcome.SUCCESS:
        booking = attempt.booking
        booking._sloty_idempotency_reused = True
        return booking
    raise_previous_booking_attempt_rejection(attempt)


def booking_attempt_payload(
    *,
    club,
    court,
    created_by,
    start_time,
    end_time,
    requested_at,
    booking_data,
):
    return {
        "club": club,
        "court": court,
        "attempted_by": created_by,
        "client_request_id": booking_data.get("client_request_id"),
        "customer_name": booking_data.get("customer_name"),
        "customer_phone": booking_data.get("customer_phone"),
        "notes": booking_data.get("notes", "") or "",
        "requested_start": start_time,
        "requested_end": end_time,
        "requested_at": requested_at,
        "requested_source": requested_source_from_booking_data(booking_data),
        "requested_recurring": requested_recurring_from_booking_data(booking_data),
    }


def create_success_booking_attempt(
    *,
    booking,
    created_by,
    start_time,
    end_time,
    requested_at,
    booking_data,
):
    if booking_data.get("client_request_id") is not None:
        existing_attempt = BookingAttempt.objects.filter(
            club=booking.club,
            client_request_id=booking_data["client_request_id"],
        ).first()
        if existing_attempt is not None:
            return existing_attempt
    return BookingAttempt.objects.create(
        **booking_attempt_payload(
            club=booking.club,
            court=booking.court,
            created_by=created_by,
            start_time=start_time,
            end_time=end_time,
            requested_at=requested_at,
            booking_data=booking_data,
        ),
        outcome=BookingAttempt.Outcome.SUCCESS,
        booking=booking,
        resolution=BookingAttempt.Resolution.RESOLVED,
    )


def failure_details_for_exception(exc):
    if isinstance(exc, SlotyAPIException):
        return normalize_failure_details(exc.details or {})
    if isinstance(exc, serializers.ValidationError):
        return normalize_failure_details(exc.detail)
    return {}


def normalize_failure_details(value):
    if isinstance(value, dict):
        return {
            str(key): normalize_failure_details(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_failure_details(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def failure_code_for_exception(exc):
    if isinstance(exc, SlotyAPIException):
        return exc.api_code
    if isinstance(exc, serializers.ValidationError):
        return "VALIDATION_ERROR"
    return "BOOKING_REQUEST_REJECTED"


def should_record_rejected_booking_attempt(exc):
    if isinstance(exc, SlotyAPIException):
        return exc.api_code != "BOOKING_CLIENT_REQUEST_MISMATCH"
    return isinstance(exc, serializers.ValidationError)


def record_rejected_booking_attempt(
    *,
    created_by,
    court,
    start_time,
    end_time,
    requested_at,
    booking_data,
    exc,
):
    if not should_record_rejected_booking_attempt(exc):
        return None
    club = court.club
    client_request_id = booking_data.get("client_request_id")

    try:
        with transaction.atomic():
            if client_request_id is not None:
                club.__class__.objects.select_for_update().get(pk=club.pk)
                existing_attempt = (
                    BookingAttempt.objects.select_for_update(of=("self",))
                    .filter(club=club, client_request_id=client_request_id)
                    .first()
                )
                if existing_attempt is not None:
                    if booking_attempt_matches_client_request(
                        existing_attempt,
                        court=court,
                        start_time=start_time,
                        end_time=end_time,
                        booking_data=booking_data,
                    ):
                        return existing_attempt
                    raise_booking_attempt_mismatch(
                        client_request_id=client_request_id,
                        attempt=existing_attempt,
                    )
            return BookingAttempt.objects.create(
                **booking_attempt_payload(
                    club=club,
                    court=court,
                    created_by=created_by,
                    start_time=start_time,
                    end_time=end_time,
                    requested_at=requested_at,
                    booking_data=booking_data,
                ),
                outcome=BookingAttempt.Outcome.REJECTED,
                failure_code=failure_code_for_exception(exc),
                failure_details=failure_details_for_exception(exc),
            )
    except IntegrityError:
        if client_request_id is None:
            raise
        return BookingAttempt.objects.filter(
            club=club,
            client_request_id=client_request_id,
        ).first()


def dismiss_booking_attempt(*, access, attempt, actor):
    with transaction.atomic():
        locked_attempt = (
            BookingAttempt.objects.select_for_update(of=("self",))
            .select_related("club", "court", "attempted_by", "booking")
            .get(pk=attempt.pk)
        )
        if not access.can_dismiss_booking_attempt(locked_attempt):
            raise PermissionDenied("You cannot dismiss this booking attempt.")
        if locked_attempt.resolution == BookingAttempt.Resolution.DISMISSED:
            return locked_attempt
        if (
            locked_attempt.outcome != BookingAttempt.Outcome.REJECTED
            or locked_attempt.booking_id is not None
        ):
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="BOOKING_ATTEMPT_CANNOT_BE_DISMISSED",
                message=BOOKING_ATTEMPT_CANNOT_BE_DISMISSED_MESSAGE,
            )

        locked_attempt.resolution = BookingAttempt.Resolution.DISMISSED
        locked_attempt.save(update_fields=["resolution", "modified"])
        return locked_attempt


def validate_no_active_recurrence_overlap(
    court,
    start_time,
    end_time,
    *,
    exclude_booking=None,
    anchors=None,
):
    conflict = find_active_recurrence_conflict(
        court=court,
        start_time=start_time,
        end_time=end_time,
        exclude_booking=exclude_booking,
        anchors=anchors,
    )
    if conflict is not None:
        raise_slot_unavailable(
            details=recurrence_conflict_details(
                conflict_type="RECURRING_PATTERN",
                start_time=start_time,
                booking=conflict,
            )
        )


def validate_no_availability_conflict(
    court,
    start_time,
    end_time,
    *,
    exclude_booking=None,
):
    validate_no_booking_overlap(
        court,
        start_time,
        end_time,
        exclude_booking=exclude_booking,
    )
    validate_no_active_recurrence_overlap(
        court,
        start_time,
        end_time,
        exclude_booking=exclude_booking,
    )


def recurring_time_intervals_overlap(start_a, end_a, start_b, end_b):
    local_start_a, local_end_a = localized_interval(start_a, end_a)
    local_start_b, local_end_b = localized_interval(start_b, end_b)
    if (local_start_b.date() - local_start_a.date()).days % 7 != 0:
        return False
    a_start = datetime.combine(local_start_a.date(), local_start_a.time())
    a_end = datetime.combine(local_start_a.date(), local_end_a.time())
    b_start = datetime.combine(local_start_a.date(), local_start_b.time())
    b_end = datetime.combine(local_start_a.date(), local_end_b.time())
    return a_start < b_end and a_end > b_start


def find_new_recurrence_conflict(
    *,
    court,
    start_time,
    end_time,
    exclude_booking=None,
    active_anchors=None,
    future_blockers=None,
):
    anchors = active_anchors
    if anchors is None:
        anchors = list(
            active_recurring_anchor_queryset(court, exclude_booking=exclude_booking)
        )
    for anchor in anchors:
        if exclude_booking is not None and anchor.pk == exclude_booking.pk:
            continue
        if recurring_time_intervals_overlap(
            start_time,
            end_time,
            anchor.start_time,
            anchor.end_time,
        ):
            later_start = max(
                timezone.localtime(start_time), timezone.localtime(anchor.start_time)
            )
            return {
                "blocked_reason": "ACTIVE_RECURRENCE",
                "first_conflict_start": later_start,
                "booking": anchor,
            }

    blockers = future_blockers
    if blockers is None:
        blockers = list(
            Booking.objects.filter(
                court=court,
                status__in=Booking.BLOCKING_STATUSES,
                end_time__gt=start_time,
            )
            .exclude(pk=exclude_booking.pk if exclude_booking is not None else None)
            .select_related("club", "court")
            .order_by("start_time", "id")
        )
    pseudo_anchor = type(
        "RecurrenceCandidate",
        (),
        {
            "source": Booking.Source.RECURRING,
            "recurrence_status": Booking.RecurrenceStatus.ACTIVE,
            "start_time": start_time,
            "end_time": end_time,
        },
    )()
    earliest = None
    for blocker in blockers:
        if exclude_booking is not None and blocker.pk == exclude_booking.pk:
            continue
        if weekly_pattern_matches(
            anchor_booking=pseudo_anchor,
            candidate_start=blocker.start_time,
            candidate_end=blocker.end_time,
        ):
            if (
                earliest is None
                or blocker.start_time < earliest["first_conflict_start"]
            ):
                earliest = {
                    "blocked_reason": "FUTURE_CONFLICT",
                    "first_conflict_start": blocker.start_time,
                    "booking": blocker,
                }
    return earliest


def validate_can_start_recurrence(
    *,
    court,
    start_time,
    end_time,
    exclude_booking=None,
    active_anchors=None,
    future_blockers=None,
):
    conflict = find_new_recurrence_conflict(
        court=court,
        start_time=start_time,
        end_time=end_time,
        exclude_booking=exclude_booking,
        active_anchors=active_anchors,
        future_blockers=future_blockers,
    )
    if conflict is not None:
        raise_recurring_unavailable(
            details=recurrence_conflict_details(
                conflict_type=conflict["blocked_reason"],
                start_time=conflict["first_conflict_start"],
                booking=conflict["booking"],
            )
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
    if slot_status == RECURRING_RESERVED_SLOT_STATUS:
        return str(_("Recurring reserved"))
    return str(Booking.Status(slot_status).label)


def booking_slot_payload(booking):
    paid_amount = getattr(booking, "paid_amount", Decimal("0.00")) or Decimal("0.00")
    remaining_amount = max(booking.total_price - paid_amount, Decimal("0.00"))
    return {
        "id": booking.id,
        "status": booking.status,
        "status_label": str(booking.get_status_display()),
        "customer_name": booking.customer_name,
        "customer_phone": str(booking.customer_phone),
        "total_booking_value": f"{booking.total_price:.2f}",
        "total_paid_amount": f"{paid_amount:.2f}",
        "remaining_amount": f"{remaining_amount:.2f}",
        "source": booking.source,
        "is_recurring": booking.source == Booking.Source.RECURRING,
        "recurrence_status": booking.recurrence_status,
    }


def recurring_slot_context_payload(anchor):
    return {
        "anchor_booking_id": anchor.id,
        "customer_name": anchor.customer_name,
        "customer_phone": str(anchor.customer_phone),
        "recurrence_status": anchor.recurrence_status,
    }


def booking_overlaps_slot(booking, slot_start, slot_end):
    return booking.start_time < slot_end and booking.end_time > slot_start


def index_bookings_by_local_date(bookings):
    index = defaultdict(list)
    for booking in bookings:
        start_local = timezone.localtime(booking.start_time)
        end_local = timezone.localtime(booking.end_time)
        current = start_local.date()
        last = end_local.date()
        if end_local.time() == time.min:
            last = last - timedelta(days=1)
        if last < current:
            last = current
        while current <= last:
            index[current].append(booking)
            current += timedelta(days=1)
    return index


def index_bookings_by_local_weekday(bookings):
    index = defaultdict(list)
    for booking in bookings:
        index[timezone.localtime(booking.start_time).weekday()].append(booking)
    return index


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
    future_blockers = list(
        annotate_booking_paid_amount(
            Booking.objects.filter(
                club=access.club,
                court=court,
                status__in=Booking.BLOCKING_STATUSES,
                end_time__gt=range_start,
            )
        ).order_by("start_time", "id")
    )
    blocking_bookings = [
        booking for booking in future_blockers if booking.start_time < range_end
    ]
    active_recurring_anchors = list(active_recurring_anchor_queryset(court))
    blocking_by_date = index_bookings_by_local_date(blocking_bookings)
    anchors_by_weekday = index_bookings_by_local_weekday(active_recurring_anchors)
    blockers_by_weekday = index_bookings_by_local_weekday(future_blockers)
    slot_price_cache = {}

    slots = []
    current_date = date_from
    has_closed_day = False
    while current_date <= date_to:
        working_hour = working_hours_by_weekday.get(current_date.weekday())
        bounds = working_hour_bounds(working_hour) if working_hour is not None else None
        if bounds is None:
            has_closed_day = True
            current_date += timedelta(days=1)
            continue

        opens_at, closes_at, _pricing_periods = bounds
        day_open = local_datetime_for_date(current_date, opens_at)
        day_close = local_datetime_for_date(current_date, closes_at)
        slot_delta = timedelta(minutes=court.slot_duration_minutes)
        slot_start = day_open
        weekday = current_date.weekday()
        day_bookings = blocking_by_date.get(current_date, ())
        day_anchors = anchors_by_weekday.get(weekday, ())
        day_blockers = blockers_by_weekday.get(weekday, ())
        while slot_start + slot_delta <= day_close:
            slot_end = slot_start + slot_delta
            booking = next(
                (
                    candidate
                    for candidate in day_bookings
                    if booking_overlaps_slot(candidate, slot_start, slot_end)
                ),
                None,
            )
            price_key = (weekday, slot_start.time(), slot_end.time())
            if price_key in slot_price_cache:
                slot_price = slot_price_cache[price_key]
            else:
                slot_price = slot_price_from_schedule(
                    court=court,
                    start_time=slot_start,
                    end_time=slot_end,
                    working_hours=working_hours,
                )
                slot_price_cache[price_key] = slot_price
            recurring_context = None
            if booking is not None:
                slot_status = booking.status
                is_available = False
                recurring_anchor_booking_id = None
                can_start_recurring = None
                recurring_blocked_reason = None
                first_recurring_conflict_start = None
            elif slot_price is None:
                slot_status = UNAVAILABLE_SLOT_STATUS
                is_available = False
                recurring_anchor_booking_id = None
                can_start_recurring = None
                recurring_blocked_reason = None
                first_recurring_conflict_start = None
            elif (
                recurring_anchor := find_active_recurrence_conflict(
                    court=court,
                    start_time=slot_start,
                    end_time=slot_end,
                    anchors=day_anchors,
                )
            ) is not None:
                slot_status = RECURRING_RESERVED_SLOT_STATUS
                is_available = False
                recurring_anchor_booking_id = recurring_anchor.id
                recurring_context = recurring_slot_context_payload(recurring_anchor)
                can_start_recurring = None
                recurring_blocked_reason = None
                first_recurring_conflict_start = None
            else:
                slot_status = FREE_SLOT_STATUS
                is_available = True
                recurrence_conflict = find_new_recurrence_conflict(
                    court=court,
                    start_time=slot_start,
                    end_time=slot_end,
                    active_anchors=day_anchors,
                    future_blockers=day_blockers,
                )
                can_start_recurring = recurrence_conflict is None
                recurring_blocked_reason = (
                    None
                    if recurrence_conflict is None
                    else recurrence_conflict["blocked_reason"]
                )
                first_recurring_conflict_start = (
                    None
                    if recurrence_conflict is None
                    else recurrence_conflict["first_conflict_start"]
                )
                recurring_anchor_booking_id = None
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
                    "recurring_anchor_booking_id": recurring_anchor_booking_id,
                    "recurring_context": recurring_context,
                    "can_start_recurring": can_start_recurring,
                    "recurring_blocked_reason": recurring_blocked_reason,
                    "first_recurring_conflict_start": first_recurring_conflict_start,
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


def create_booking(
    *,
    created_by,
    court,
    start_time,
    end_time,
    requested_at=None,
    **booking_data,
):
    requested_at = requested_at or timezone.now()
    try:
        with transaction.atomic():
            locked_court = (
                court.__class__.objects.select_for_update()
                .select_related("club")
                .prefetch_related("working_hours__pricing_periods")
                .get(pk=court.pk)
            )
            if booking_data.get("client_request_id") is not None:
                locked_court.club.__class__.objects.select_for_update().get(
                    pk=locked_court.club_id
                )
                existing_attempt_booking = resolve_idempotent_booking_attempt(
                    club=locked_court.club,
                    court=locked_court,
                    start_time=start_time,
                    end_time=end_time,
                    booking_data=booking_data,
                )
                if existing_attempt_booking is not None:
                    return existing_attempt_booking
                existing_booking = resolve_idempotent_booking_request(
                    club=locked_court.club,
                    court=locked_court,
                    start_time=start_time,
                    end_time=end_time,
                    booking_data=booking_data,
                )
                if existing_booking is not None:
                    create_success_booking_attempt(
                        booking=existing_booking,
                        created_by=created_by,
                        start_time=start_time,
                        end_time=end_time,
                        requested_at=requested_at,
                        booking_data=booking_data,
                    )
                    return existing_booking

            validate_booking_duration(locked_court, start_time, end_time)
            total_price = calculate_booking_price(
                locked_court,
                start_time,
                end_time,
            )
            if booking_data.get("source") == Booking.Source.RECURRING:
                validate_no_availability_conflict(locked_court, start_time, end_time)
                validate_can_start_recurrence(
                    court=locked_court,
                    start_time=start_time,
                    end_time=end_time,
                )
                booking_data["recurrence_status"] = Booking.RecurrenceStatus.ACTIVE
            else:
                validate_no_availability_conflict(locked_court, start_time, end_time)

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
            created_booking._sloty_idempotency_reused = False
            create_success_booking_attempt(
                booking=created_booking,
                created_by=created_by,
                start_time=start_time,
                end_time=end_time,
                requested_at=requested_at,
                booking_data=booking_data,
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
    except (SlotyAPIException, serializers.ValidationError) as exc:
        record_rejected_booking_attempt(
            created_by=created_by,
            court=court,
            start_time=start_time,
            end_time=end_time,
            requested_at=requested_at,
            booking_data=booking_data,
            exc=exc,
        )
        raise


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


def calculate_cancellation_refund(*, booking, requested_at):
    # Refund deadline is derived from the current operational start_time.
    # Reschedule overwrites start_time; preserving previously lost refund
    # rights requires stored booking state (do not use AuditLog or client
    # amounts). See APPROVAL REQUIRED — RESCHEDULE REFUND PERSISTENCE.
    if requested_at >= booking.start_time:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_CANCELLATION_TIME_PASSED",
            message=BOOKING_CANCELLATION_TIME_PASSED_MESSAGE,
        )
    notice_days = booking.court.cancellation_refund_notice_days
    if notice_days is None:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED",
            message=BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED_MESSAGE,
        )
    paid_amount = get_booking_paid_amount(booking)
    refund_deadline = (
        booking.start_time
        if notice_days == 0
        else booking.start_time - timedelta(days=notice_days)
    )
    full_refund = requested_at <= refund_deadline
    retained_amount = (
        Decimal("0.00")
        if full_refund
        else min(paid_amount, booking.court.minimum_deposit)
    )
    refund_amount = max(paid_amount - retained_amount, Decimal("0.00"))
    return {
        "booking_id": booking.id,
        "previewed_at": requested_at,
        "booking_start": booking.start_time,
        "paid_amount": paid_amount,
        "minimum_deposit": booking.court.minimum_deposit,
        "refund_notice_days": notice_days,
        "refund_deadline": refund_deadline,
        "full_refund": full_refund,
        "refund_amount": refund_amount,
        "retained_amount": retained_amount,
        "can_cancel": True,
    }


def build_cancellation_preview(*, access, booking):
    selected = Booking.objects.select_related("club", "court").get(pk=booking.pk)
    validate_booking_for_lifecycle_action(access=access, booking=selected)
    validate_allowed_status(
        booking=selected,
        allowed_statuses={Booking.Status.HOLD, Booking.Status.CONFIRMED},
        action_label="cancel",
    )
    return calculate_cancellation_refund(
        booking=selected,
        requested_at=timezone.now(),
    )


def validate_refund_fields(*, booking, refund_amount, payment_method, reference):
    normalized_reference = normalize_payment_reference(reference)
    if refund_amount <= 0:
        return normalized_reference
    if not payment_method:
        raise serializers.ValidationError(
            {"refund_payment_method": str(REFUND_PAYMENT_METHOD_REQUIRED_MESSAGE)}
        )
    requires_reference = (
        payment_method
        in {
            Transaction.PaymentMethod.DIGITAL_WALLET,
            Transaction.PaymentMethod.BANK_TRANSFER,
        }
        and booking.court.requires_digital_payment_reference
    )
    if requires_reference and not normalized_reference:
        raise serializers.ValidationError(
            {"refund_reference": str(REFUND_REFERENCE_REQUIRED_MESSAGE)}
        )
    validate_duplicate_payment_reference(
        club=booking.club,
        payment_reference=normalized_reference,
    )
    return normalized_reference


def create_booking_refund_transaction(
    *,
    booking,
    refund_amount,
    payment_method,
    payment_reference,
    notes,
    actor,
):
    if refund_amount <= 0:
        return None
    if booking.status != Booking.Status.CANCELLED:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="REFUND_REQUIRES_CANCELLED_BOOKING",
            message=_("Refunds may only be created for cancelled bookings."),
        )
    active_refunded = get_booking_refunded_amount(booking)
    active_paid = get_booking_paid_amount(booking)
    if active_refunded + refund_amount > active_paid:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_REFUND_EXCEEDS_PAID_AMOUNT",
            message=_("Refund amount cannot exceed collected booking payments."),
        )
    refund_transaction = Transaction.objects.create(
        club=booking.club,
        court=booking.court,
        booking=booking,
        transaction_type=Transaction.Type.REFUND,
        amount=-refund_amount,
        payment_method=payment_method,
        payment_reference=payment_reference,
        notes=notes,
        created_by=actor,
    )
    record_audit_log(
        club=refund_transaction.club,
        court=refund_transaction.court,
        actor=actor,
        action=AuditLog.Action.TRANSACTION_CREATED,
        entity_type="Transaction",
        entity_id=refund_transaction.id,
        after_data=transaction_audit_snapshot(refund_transaction),
    )
    return refund_transaction


def end_active_recurrence(*, locked_booking):
    if (
        locked_booking.source == Booking.Source.RECURRING
        and locked_booking.recurrence_status == Booking.RecurrenceStatus.ACTIVE
    ):
        locked_booking.recurrence_status = Booking.RecurrenceStatus.ENDED
        return True
    return False


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


def cancel_booking(
    *,
    access,
    booking,
    actor,
    reason="",
    refund_payment_method=None,
    refund_reference="",
    refund_notes="",
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
            action_label="cancel",
        )

        reason = (reason or "").strip()
        if actor_requires_staff_cancel_reason(access) and not reason:
            raise serializers.ValidationError(
                {"reason": "Staff must provide a cancellation reason."}
            )
        now = timezone.now()
        refund_data = calculate_cancellation_refund(
            booking=locked_booking,
            requested_at=now,
        )
        normalized_refund_reference = validate_refund_fields(
            booking=locked_booking,
            refund_amount=refund_data["refund_amount"],
            payment_method=refund_payment_method,
            reference=refund_reference,
        )

        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.status = Booking.Status.CANCELLED
        locked_booking.cancelled_at = now
        locked_booking.cancellation_reason = reason
        recurrence_ended = end_active_recurrence(locked_booking=locked_booking)
        update_fields = [
            "status",
            "cancelled_at",
            "cancellation_reason",
            "modified",
        ]
        if recurrence_ended:
            update_fields.append("recurrence_status")
        locked_booking.save(update_fields=update_fields)
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
            metadata={
                "reason": reason,
                "refund_amount": str(refund_data["refund_amount"]),
                "retained_amount": str(refund_data["retained_amount"]),
                "refund_deadline": refund_data["refund_deadline"].isoformat(),
                "full_refund": refund_data["full_refund"],
                "recurrence_ended": recurrence_ended,
            },
        )
        create_booking_refund_transaction(
            booking=locked_booking,
            refund_amount=refund_data["refund_amount"],
            payment_method=refund_payment_method,
            payment_reference=normalized_refund_reference,
            notes=refund_notes or "",
            actor=actor,
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
        recurrence_ended = end_active_recurrence(locked_booking=locked_booking)
        update_fields = ["status", "no_show_at", "no_show_reason", "modified"]
        if recurrence_ended:
            update_fields.append("recurrence_status")
        locked_booking.save(update_fields=update_fields)
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
            metadata={
                **(
                    {"reason": locked_booking.no_show_reason}
                    if locked_booking.no_show_reason
                    else {}
                ),
                "recurrence_ended": recurrence_ended,
            },
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
        if (
            locked_booking.source == Booking.Source.RECURRING
            and locked_booking.recurrence_status == Booking.RecurrenceStatus.ACTIVE
        ):
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="RECURRING_BOOKING_RESCHEDULE_NOT_SUPPORTED",
                message=RECURRING_BOOKING_RESCHEDULE_NOT_SUPPORTED_MESSAGE,
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
        validate_no_availability_conflict(
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


def next_recurring_interval_after_reference(*, booking, reference_time=None):
    recurrence_step = timedelta(days=7)
    next_start = booking.start_time + recurrence_step
    next_end = booking.end_time + recurrence_step
    reference_time = reference_time or timezone.now()

    if next_end <= reference_time:
        step_seconds = recurrence_step.total_seconds()
        missed_steps = (
            int((reference_time - next_end).total_seconds() // step_seconds) + 1
        )
        next_start += recurrence_step * missed_steps
        next_end += recurrence_step * missed_steps

    return next_start, next_end


def plan_next_recurring_occurrence(*, court, booking):
    if not court.is_active or not court.club.is_active:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="RECURRENCE_CANNOT_CONTINUE",
            message=RECURRENCE_CANNOT_CONTINUE_MESSAGE,
        )

    next_start, next_end = next_recurring_interval_after_reference(booking=booking)
    try:
        validate_booking_duration(court, next_start, next_end)
        next_price = calculate_booking_price(court, next_start, next_end)
        validate_no_availability_conflict(
            court,
            next_start,
            next_end,
            exclude_booking=booking,
        )
    except serializers.ValidationError as exc:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NEXT_RECURRING_SLOT_UNAVAILABLE",
            message=NEXT_RECURRING_SLOT_UNAVAILABLE_MESSAGE,
            details={"validation": exc.detail},
        ) from exc
    except SlotyAPIException as exc:
        if exc.api_code in NEXT_OCCURRENCE_PLAN_ERROR_CODES:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="NEXT_RECURRING_SLOT_UNAVAILABLE",
                message=NEXT_RECURRING_SLOT_UNAVAILABLE_MESSAGE,
                details=exc.details,
            ) from exc
        raise

    required_deposit = min(court.minimum_deposit, next_price)
    requires_digital_payment_reference = bool(court.requires_digital_payment_reference)
    return {
        "next_start_time": next_start,
        "next_end_time": next_end,
        "next_total_price": next_price,
        "next_required_deposit": required_deposit,
        "requires_digital_payment_reference": requires_digital_payment_reference,
        "requires_payment_reference": requires_digital_payment_reference,
    }


def preview_recurrence_next(*, access, booking):
    validate_booking_for_lifecycle_action(access=access, booking=booking)
    if (
        booking.source != Booking.Source.RECURRING
        or booking.recurrence_status != Booking.RecurrenceStatus.ACTIVE
    ):
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_RECURRENCE_NOT_ACTIVE",
            message=BOOKING_RECURRENCE_NOT_ACTIVE_MESSAGE,
        )
    if booking.status != Booking.Status.CONFIRMED:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="RECURRENCE_CANNOT_CONTINUE",
            message=RECURRENCE_CANNOT_CONTINUE_MESSAGE,
        )

    court = (
        Court.objects.select_related("club")
        .prefetch_related("working_hours__pricing_periods")
        .get(pk=booking.court_id)
    )
    plan = plan_next_recurring_occurrence(court=court, booking=booking)
    return {
        "can_continue": True,
        "next_start_time": plan["next_start_time"],
        "next_end_time": plan["next_end_time"],
        "next_total_price": f"{plan['next_total_price']:.2f}",
        "next_required_deposit": f"{plan['next_required_deposit']:.2f}",
        "requires_digital_payment_reference": plan[
            "requires_digital_payment_reference"
        ],
        "requires_payment_reference": plan["requires_payment_reference"],
    }


def complete_booking(
    *,
    access,
    booking,
    actor,
    confirm_collect_remaining_cash=False,
    continue_recurring=None,
    next_deposit_payment_method=None,
    next_deposit_payment_reference="",
    next_deposit_notes="",
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

        is_active_recurrence = (
            locked_booking.source == Booking.Source.RECURRING
            and locked_booking.recurrence_status == Booking.RecurrenceStatus.ACTIVE
        )
        if is_active_recurrence and continue_recurring is None:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="RECURRENCE_CONTINUATION_DECISION_REQUIRED",
                message=RECURRENCE_CONTINUATION_DECISION_REQUIRED_MESSAGE,
            )

        before_data = booking_audit_snapshot(locked_booking)
        next_booking = None
        required_next_deposit = Decimal("0.00")
        if is_active_recurrence and continue_recurring:
            locked_court = (
                Court.objects.select_for_update()
                .select_related("club")
                .prefetch_related("working_hours__pricing_periods")
                .get(pk=locked_booking.court_id)
            )
            next_plan = plan_next_recurring_occurrence(
                court=locked_court,
                booking=locked_booking,
            )
            next_start = next_plan["next_start_time"]
            next_end = next_plan["next_end_time"]
            next_price = next_plan["next_total_price"]
            next_booking = Booking.objects.create(
                club=locked_booking.club,
                court=locked_court,
                customer_name=locked_booking.customer_name,
                customer_phone=locked_booking.customer_phone,
                start_time=next_start,
                end_time=next_end,
                total_price=next_price,
                status=Booking.Status.HOLD,
                source=Booking.Source.RECURRING,
                recurrence_status=Booking.RecurrenceStatus.ACTIVE,
                previous_recurring_booking=locked_booking,
                created_by=actor,
            )
            required_next_deposit = next_plan["next_required_deposit"]
            if required_next_deposit > 0:
                if not next_deposit_payment_method:
                    raise serializers.ValidationError(
                        {
                            "next_deposit_payment_method": _(
                                "Next deposit payment method is required."
                            )
                        }
                    )
                normalized_reference = normalize_payment_reference(
                    next_deposit_payment_reference
                )
                requires_reference = (
                    next_deposit_payment_method
                    in {
                        Transaction.PaymentMethod.DIGITAL_WALLET,
                        Transaction.PaymentMethod.BANK_TRANSFER,
                    }
                    and locked_court.requires_digital_payment_reference
                )
                if requires_reference and not normalized_reference:
                    raise serializers.ValidationError(
                        {
                            "next_deposit_payment_reference": str(
                                REFUND_REFERENCE_REQUIRED_MESSAGE
                            )
                        }
                    )
                validate_duplicate_payment_reference(
                    club=locked_booking.club,
                    payment_reference=normalized_reference,
                )
                next_payment = Transaction.objects.create(
                    club=locked_booking.club,
                    court=locked_court,
                    booking=next_booking,
                    transaction_type=Transaction.Type.PAYMENT,
                    amount=required_next_deposit,
                    payment_method=next_deposit_payment_method,
                    payment_reference=normalized_reference,
                    notes=next_deposit_notes or "",
                    created_by=actor,
                )
                next_booking.status = Booking.Status.CONFIRMED
                next_booking.save(update_fields=["status", "modified"])
                record_audit_log(
                    club=next_payment.club,
                    court=next_payment.court,
                    actor=actor,
                    action=AuditLog.Action.TRANSACTION_CREATED,
                    entity_type="Transaction",
                    entity_id=next_payment.id,
                    after_data=transaction_audit_snapshot(next_payment),
                    metadata={"source": "recurrence_renewal"},
                )
            else:
                next_booking.status = Booking.Status.CONFIRMED
                next_booking.save(update_fields=["status", "modified"])
            record_audit_log(
                club=next_booking.club,
                court=next_booking.court,
                actor=actor,
                action=AuditLog.Action.BOOKING_CREATED,
                entity_type="Booking",
                entity_id=next_booking.id,
                after_data=booking_audit_snapshot(next_booking),
                metadata={"source": "recurrence_renewal"},
            )

        locked_booking.status = Booking.Status.COMPLETED
        locked_booking.completed_at = timezone.now()
        update_fields = ["status", "completed_at", "modified"]
        if is_active_recurrence:
            locked_booking.recurrence_status = (
                Booking.RecurrenceStatus.RENEWED
                if continue_recurring
                else Booking.RecurrenceStatus.ENDED
            )
            update_fields.append("recurrence_status")
        locked_booking.save(update_fields=update_fields)
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_COMPLETED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking)
            | {"completed_at": locked_booking.completed_at.isoformat()},
            metadata=(
                {
                    "source": "recurrence_renewal",
                    "previous_booking_id": locked_booking.id,
                    "next_booking_id": next_booking.id if next_booking else None,
                    "required_next_deposit": str(required_next_deposit),
                    "next_booking_price": (
                        str(next_booking.total_price) if next_booking else None
                    ),
                }
                if is_active_recurrence and continue_recurring
                else ({"source": "recurrence_end"} if is_active_recurrence else {})
            ),
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
    recurrence_ended = end_active_recurrence(locked_booking=locked_booking)
    update_fields = ["status", "expired_at", "modified"]
    if recurrence_ended:
        update_fields.append("recurrence_status")
    locked_booking.save(update_fields=update_fields)
    create_lifecycle_audit_log(
        booking=locked_booking,
        actor=actor,
        action=AuditLog.Action.BOOKING_EXPIRED,
        before_data=before_data,
        after_data=booking_audit_snapshot(locked_booking)
        | {"expired_at": locked_booking.expired_at.isoformat()},
        metadata={**(metadata or {}), "recurrence_ended": recurrence_ended},
    )
    return locked_booking


def end_booking_recurrence(*, access, booking, actor, reason=""):
    with transaction.atomic():
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=booking.pk)
        )
        validate_booking_for_lifecycle_action(access=access, booking=locked_booking)
        if (
            locked_booking.source != Booking.Source.RECURRING
            or locked_booking.recurrence_status != Booking.RecurrenceStatus.ACTIVE
            or locked_booking.status
            not in {Booking.Status.HOLD, Booking.Status.CONFIRMED}
        ):
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="BOOKING_RECURRENCE_NOT_ACTIVE",
                message=BOOKING_RECURRENCE_NOT_ACTIVE_MESSAGE,
            )
        before_data = booking_audit_snapshot(locked_booking)
        locked_booking.recurrence_status = Booking.RecurrenceStatus.ENDED
        locked_booking.save(update_fields=["recurrence_status", "modified"])
        create_lifecycle_audit_log(
            booking=locked_booking,
            actor=actor,
            action=AuditLog.Action.BOOKING_UPDATED,
            before_data=before_data,
            after_data=booking_audit_snapshot(locked_booking),
            metadata={
                "source": "recurrence_end",
                "reason": (reason or "").strip(),
            },
        )
        return locked_booking


def due_hold_booking_candidate_ids(*, now):
    return list(
        annotate_booking_hold_expires_at(
            Booking.objects.filter(status=Booking.Status.HOLD)
        )
        .filter(hold_expires_at__lte=now)
        .order_by("id")
        .values_list("id", flat=True)
    )


def expire_locked_due_hold_bookings(*, due_ids, now):
    expired_bookings = []
    with transaction.atomic():
        locked_bookings = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .filter(id__in=due_ids, status=Booking.Status.HOLD)
            .order_by("id")
        )
        for locked_booking in locked_bookings:
            if compute_booking_hold_expires_at(locked_booking) > now:
                continue
            expired_bookings.append(
                expire_locked_booking(
                    locked_booking=locked_booking,
                    actor=None,
                    metadata={"source": "automatic_hold_expiry"},
                )
            )
    return expired_bookings


def expire_due_hold_bookings(*, now=None):
    now = now or timezone.now()
    due_ids = due_hold_booking_candidate_ids(now=now)
    return expire_locked_due_hold_bookings(due_ids=due_ids, now=now)
