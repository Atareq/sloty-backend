from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.common.exceptions import SlotyAPIException
from apps.common.weekdays import to_sloty_weekday

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
    Validate a non-empty half-open time-of-day interval. An end before the
    start is resolved on the following local date; equal bounds are invalid.
    """
    return starts_at != ends_at


def normalized_pricing_periods(periods):
    """Return pricing periods ordered on one half-open operational timeline."""
    entries = []
    overnight_starts = []
    for period in periods:
        starts_at = (
            period["starts_at"] if isinstance(period, dict) else period.starts_at
        )
        ends_at = period["ends_at"] if isinstance(period, dict) else period.ends_at
        start = time_to_minutes(starts_at)
        raw_end = time_to_minutes(ends_at)
        end = time_to_minutes(ends_at, is_end=True)
        if ends_at != MIDNIGHT_TIME and raw_end < start:
            overnight_starts.append(start)
        entries.append((period, start, end))

    if not entries:
        return []

    anchor = min(overnight_starts, default=min(entry[1] for entry in entries))
    normalized = []
    for period, start, end in entries:
        if start < anchor:
            start += MIDNIGHT_END_MINUTES
        while end <= start:
            end += MIDNIGHT_END_MINUTES
        normalized.append((period, start, end))
    return sorted(normalized, key=lambda entry: (entry[1], entry[2]))


def datetime_for_operational_minute(date_value: date, minute: int) -> datetime:
    day_offset, minute_of_day = divmod(minute, MIDNIGHT_END_MINUTES)
    return datetime_for_local_date(
        date_value + timedelta(days=day_offset),
        time(minute_of_day // 60, minute_of_day % 60),
    )


def pricing_intervals_for_local_date(working_hour, date_value: date):
    """Resolve configured periods to aware [start, end) datetime intervals."""
    return [
        (
            period,
            datetime_for_operational_minute(date_value, start),
            datetime_for_operational_minute(date_value, end),
        )
        for period, start, end in normalized_pricing_periods(
            get_prefetched_pricing_periods(working_hour)
        )
    ]


def minutes_between(start, end):
    return int((end - start).total_seconds() // 60)


def get_prefetched_pricing_periods(working_hour):
    return list(working_hour.pricing_periods.all())


def working_hour_datetime_bounds(working_hour, date_value: date):
    intervals = pricing_intervals_for_local_date(working_hour, date_value)
    if not intervals:
        return None
    return intervals[0][1], intervals[-1][2], intervals


def get_working_hour_for_local_date(*, court, local_date, working_hours=None):
    weekday = to_sloty_weekday(local_date.weekday())
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

    day_open = pricing_intervals = None
    for operational_date in (
        local_start.date(),
        local_start.date() - timedelta(days=1),
    ):
        working_hour = get_working_hour_for_local_date(
            court=court,
            local_date=operational_date,
            working_hours=working_hours,
        )
        if working_hour is None:
            continue
        candidate_bounds = working_hour_datetime_bounds(working_hour, operational_date)
        if candidate_bounds is None:
            continue
        candidate_open, candidate_close, candidate_intervals = candidate_bounds
        if candidate_open <= local_start and local_end <= candidate_close:
            day_open = candidate_open
            pricing_intervals = candidate_intervals
            break

    if pricing_intervals is None:
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
    if any(
        minutes_between(day_open, boundary) % slot_duration != 0
        for boundary in (local_start, local_end)
    ):
        raise_booking_error(
            "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID",
            BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID_MESSAGE,
        )

    total = Decimal("0.00")
    cursor = local_start
    for period, period_start, period_end in pricing_intervals:
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
