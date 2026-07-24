from datetime import datetime
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.common.exceptions import SlotyAPIException

MONEY_QUANT = Decimal("0.01")

BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE = _(
    "The selected booking time is outside the court working hours."
)
BOOKING_PRICE_NOT_CONFIGURED_MESSAGE = _(
    "Pricing is not configured for the selected booking time."
)
BOOKING_MULTIDAY_NOT_SUPPORTED_MESSAGE = _(
    "Bookings cannot span multiple calendar days."
)
BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE = _(
    "Booking time must align with the court slot grid."
)


def time_to_minutes(value):
    return value.hour * 60 + value.minute


def datetime_for_local_date(date_value, time_value):
    return timezone.make_aware(
        datetime.combine(date_value, time_value),
        timezone.get_current_timezone(),
    )


def minutes_between(start, end):
    return int((end - start).total_seconds() // 60)


def is_aligned_to_slot_grid(*, boundary, opens_at, slot_duration_minutes):
    offset = time_to_minutes(boundary) - time_to_minutes(opens_at)
    return offset >= 0 and offset % slot_duration_minutes == 0


def get_prefetched_pricing_periods(working_hour):
    return list(working_hour.pricing_periods.all())


def get_working_hour_for_local_date(*, court, local_date, working_hours=None):
    weekday = local_date.weekday()
    if working_hours is not None:
        for working_hour in working_hours:
            if working_hour.weekday == weekday:
                return working_hour
        return None
    return (
        court.working_hours.filter(weekday=weekday)
        .prefetch_related("pricing_periods")
        .first()
    )


def raise_booking_error(code, message, *, status_code=status.HTTP_409_CONFLICT):
    raise SlotyAPIException(status_code=status_code, code=code, message=message)


def calculate_booking_price_from_schedule(
    *,
    court,
    start_time,
    end_time,
    working_hours=None,
) -> Decimal:
    local_start = timezone.localtime(start_time)
    local_end = timezone.localtime(end_time)
    if local_start.date() != local_end.date():
        raise_booking_error(
            "BOOKING_MULTIDAY_NOT_SUPPORTED",
            BOOKING_MULTIDAY_NOT_SUPPORTED_MESSAGE,
        )

    working_hour = get_working_hour_for_local_date(
        court=court,
        local_date=local_start.date(),
        working_hours=working_hours,
    )
    if (
        working_hour is None
        or working_hour.is_closed
        or working_hour.opens_at is None
        or working_hour.closes_at is None
    ):
        raise_booking_error(
            "BOOKING_OUTSIDE_WORKING_HOURS",
            BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE,
        )

    opens_at = datetime_for_local_date(local_start.date(), working_hour.opens_at)
    closes_at = datetime_for_local_date(local_start.date(), working_hour.closes_at)
    if local_start < opens_at or local_end > closes_at:
        raise_booking_error(
            "BOOKING_OUTSIDE_WORKING_HOURS",
            BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE,
        )

    slot_duration = court.slot_duration_minutes
    duration_minutes = minutes_between(local_start, local_end)
    if duration_minutes <= 0 or duration_minutes % slot_duration != 0:
        raise_booking_error(
            "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID",
            BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE,
        )
    if not (
        is_aligned_to_slot_grid(
            boundary=local_start.time(),
            opens_at=working_hour.opens_at,
            slot_duration_minutes=slot_duration,
        )
        and is_aligned_to_slot_grid(
            boundary=local_end.time(),
            opens_at=working_hour.opens_at,
            slot_duration_minutes=slot_duration,
        )
    ):
        raise_booking_error(
            "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID",
            BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE,
        )

    total = Decimal("0.00")
    cursor = local_start
    for period in get_prefetched_pricing_periods(working_hour):
        period_start = datetime_for_local_date(local_start.date(), period.starts_at)
        period_end = datetime_for_local_date(local_start.date(), period.ends_at)
        if period_end <= cursor:
            continue
        if period_start > cursor:
            break
        segment_start = max(cursor, period_start)
        segment_end = min(local_end, period_end)
        if segment_start >= segment_end:
            continue
        segment_minutes = minutes_between(segment_start, segment_end)
        if segment_minutes % slot_duration != 0:
            raise_booking_error(
                "BOOKING_PRICE_NOT_CONFIGURED",
                BOOKING_PRICE_NOT_CONFIGURED_MESSAGE,
            )
        total += Decimal(segment_minutes // slot_duration) * period.price
        cursor = segment_end
        if cursor >= local_end:
            return total.quantize(MONEY_QUANT)

    raise_booking_error(
        "BOOKING_PRICE_NOT_CONFIGURED",
        BOOKING_PRICE_NOT_CONFIGURED_MESSAGE,
    )


def slot_price_from_schedule(*, court, start_time, end_time, working_hours=None):
    try:
        return calculate_booking_price_from_schedule(
            court=court,
            start_time=start_time,
            end_time=end_time,
            working_hours=working_hours,
        )
    except SlotyAPIException as exc:
        if exc.api_code == "BOOKING_PRICE_NOT_CONFIGURED":
            return None
        raise
