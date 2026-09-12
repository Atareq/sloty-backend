from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.common.exceptions import SlotyAPIException

MONEY_QUANT = Decimal("0.01")
MIDNIGHT_END_MINUTES = 24 * 60  # 1440
MIDNIGHT_TIME = time(0, 0)

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


def time_to_minutes(value: time, *, is_end: bool = False) -> int:
    """
    Convert a time object to minutes from the start of the day.
    When is_end=True, 00:00 represents 24:00 / next-day midnight (1440 minutes).
    """
    if is_end and value == MIDNIGHT_TIME:
        return MIDNIGHT_END_MINUTES
    return value.hour * 60 + value.minute


def datetime_for_local_date(
    date_value: date,
    time_value: time,
    *,
    is_end: bool = False,
) -> datetime:
    """
    Combine a local date and time into a timezone-aware datetime.
    When is_end=True and time_value is 00:00, advances to next-day midnight
    (date + 1 day at 00:00).
    """
    target_date = date_value
    if is_end and time_value == MIDNIGHT_TIME:
        target_date = date_value + timedelta(days=1)
    return timezone.make_aware(
        datetime.combine(target_date, time_value),
        timezone.get_current_timezone(),
    )


def is_valid_period_bounds(starts_at: time, ends_at: time) -> bool:
    """
    Validate that starts_at is strictly before ends_at, evaluating ends_at
    with the shared end-boundary semantic (00:00 = 24:00 / 1440 min).
    00:00 -> 00:00 is invalid.
    """
    if starts_at == MIDNIGHT_TIME and ends_at == MIDNIGHT_TIME:
        return False
    return time_to_minutes(starts_at, is_end=False) < time_to_minutes(
        ends_at, is_end=True
    )


def compare_adjacent_period_bounds(current_start: time, previous_end: time) -> int:
    """
    Compare current period start with previous period end.
    Returns:
        < 0 if periods overlap
        > 0 if there is a gap
        == 0 if contiguous
    """
    return time_to_minutes(current_start, is_end=False) - time_to_minutes(
        previous_end, is_end=True
    )


def is_same_operational_day(local_start: datetime, local_end: datetime) -> bool:
    """
    Check if an interval belongs to a single operational day.
    Ending at 00:00 on date + 1 belongs to the operational day of start (24:00).
    Crossing past next-day midnight is disallowed (no general overnight scheduling).
    """
    if local_start.date() == local_end.date():
        return True
    return (
        local_end.date() == local_start.date() + timedelta(days=1)
        and local_end.time() == MIDNIGHT_TIME
    )


def operational_end_date(local_datetime: datetime) -> date:
    """
    Return the operational date for an end datetime.
    If the end datetime is at midnight (00:00:00), it belongs to the previous
    calendar day.
    """
    if local_datetime.time() == MIDNIGHT_TIME:
        return local_datetime.date() - timedelta(days=1)
    return local_datetime.date()


def minutes_between(start, end):
    return int((end - start).total_seconds() // 60)


def is_aligned_to_slot_grid(
    *,
    boundary: time,
    opens_at: time,
    slot_duration_minutes: int,
    is_end: bool = False,
) -> bool:
    """
    Check if a boundary aligns with the court slot duration from opens_at.
    When is_end=True, 00:00 is evaluated as 24:00 (1440 minutes).
    """
    offset = time_to_minutes(boundary, is_end=is_end) - time_to_minutes(
        opens_at, is_end=False
    )
    return offset >= 0 and offset % slot_duration_minutes == 0


def get_prefetched_pricing_periods(working_hour):
    return list(working_hour.pricing_periods.all())


def working_hour_bounds(working_hour):
    periods = get_prefetched_pricing_periods(working_hour)
    if not periods:
        return None
    return periods[0].starts_at, periods[-1].ends_at, periods


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
    if not is_same_operational_day(local_start, local_end):
        raise_booking_error(
            "BOOKING_MULTIDAY_NOT_SUPPORTED",
            BOOKING_MULTIDAY_NOT_SUPPORTED_MESSAGE,
        )

    working_hour = get_working_hour_for_local_date(
        court=court,
        local_date=local_start.date(),
        working_hours=working_hours,
    )
    bounds = working_hour_bounds(working_hour) if working_hour is not None else None
    if bounds is None:
        raise_booking_error(
            "BOOKING_OUTSIDE_WORKING_HOURS",
            BOOKING_OUTSIDE_WORKING_HOURS_MESSAGE,
        )

    opens_at, closes_at, pricing_periods = bounds
    day_open = datetime_for_local_date(local_start.date(), opens_at, is_end=False)
    day_close = datetime_for_local_date(local_start.date(), closes_at, is_end=True)
    if local_start < day_open or local_end > day_close:
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
            opens_at=opens_at,
            slot_duration_minutes=slot_duration,
            is_end=False,
        )
        and is_aligned_to_slot_grid(
            boundary=local_end.time(),
            opens_at=opens_at,
            slot_duration_minutes=slot_duration,
            is_end=True,
        )
    ):
        raise_booking_error(
            "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID",
            BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE,
        )

    total = Decimal("0.00")
    cursor = local_start
    for period in pricing_periods:
        period_start = datetime_for_local_date(
            local_start.date(), period.starts_at, is_end=False
        )
        period_end = datetime_for_local_date(
            local_start.date(), period.ends_at, is_end=True
        )
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
