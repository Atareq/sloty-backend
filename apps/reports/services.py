from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import pgettext

from apps.bookings.models import Booking
from apps.reports.constants import (
    DEMAND_BUCKET_MINUTES,
    EVENING_START_TIME,
    PERIOD_ALL_DAY,
    PERIOD_CUSTOM,
    PERIOD_DAYTIME,
    PERIOD_EVENING,
)

ZERO = Decimal("0.00")


def money(value):
    return value or ZERO


def percent(occupied_minutes, available_minutes):
    if not available_minutes:
        return Decimal("0.00")
    return (
        Decimal(occupied_minutes) * Decimal("100") / Decimal(available_minutes)
    ).quantize(Decimal("0.01"))


def empty_financial():
    return {
        "total_booking_value": ZERO,
        "total_paid_amount": ZERO,
        "total_remaining_amount": ZERO,
    }


def empty_metrics():
    return {
        "booking_ids": set(),
        "booking_count": 0,
        "occupied_minutes": 0,
        "available_minutes": 0,
        "status_counts": defaultdict(int),
        "financial": empty_financial(),
    }


def user_name(user):
    if user is None:
        return pgettext("reports missing staff", "Unknown")
    return user.get_full_name() or user.username


def aware_combine(date_value, time_value):
    return timezone.make_aware(
        datetime.combine(date_value, time_value),
        timezone.get_current_timezone(),
    )


def daterange(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def overlap_minutes(start_a, end_a, start_b, end_b):
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if start >= end:
        return 0
    return int((end - start).total_seconds() // 60)


def fixed_clock_bucket_start(value):
    return value.replace(minute=0, second=0, microsecond=0)


def period_window_for_working_hours(
    date_value, working_hour, period, hour_from, hour_to
):
    if working_hour is None or working_hour.is_closed:
        return None
    if working_hour.opens_at is None or working_hour.closes_at is None:
        return None

    opens_at = aware_combine(date_value, working_hour.opens_at)
    closes_at = aware_combine(date_value, working_hour.closes_at)
    if period == PERIOD_DAYTIME:
        period_start = opens_at
        period_end = min(closes_at, aware_combine(date_value, EVENING_START_TIME))
    elif period == PERIOD_EVENING:
        period_start = max(opens_at, aware_combine(date_value, EVENING_START_TIME))
        period_end = closes_at
    elif period == PERIOD_CUSTOM:
        period_start = max(opens_at, aware_combine(date_value, hour_from))
        period_end = min(closes_at, aware_combine(date_value, hour_to))
    else:
        period_start = opens_at
        period_end = closes_at

    if period_start >= period_end:
        return None
    return period_start, period_end


def finalize(metrics):
    financial = metrics["financial"]
    return {
        "booking_count": len(metrics["booking_ids"]),
        "occupied_minutes": metrics["occupied_minutes"],
        "available_minutes": metrics["available_minutes"],
        "utilization_percentage": percent(
            metrics["occupied_minutes"],
            metrics["available_minutes"],
        ),
        "status_counts": dict(metrics.get("status_counts", {})),
        "financial": financial,
    }


def add_booking_once(metrics, booking, *, include_financial=True):
    if booking.id in metrics["booking_ids"]:
        return
    metrics["booking_ids"].add(booking.id)
    metrics["status_counts"][booking.status] += 1
    if not include_financial:
        return
    paid_amount = money(booking.paid_amount)
    metrics["financial"]["total_booking_value"] += booking.total_price
    metrics["financial"]["total_paid_amount"] += paid_amount
    metrics["financial"]["total_remaining_amount"] += max(
        booking.total_price - paid_amount,
        ZERO,
    )


def get_court_usage_report(*, access, query):
    selected_courts = access.scoped_report_courts_queryset().order_by("id")
    if query.get("court") is not None:
        selected_courts = selected_courts.filter(pk=query["court"].pk)
    selected_courts = list(selected_courts.prefetch_related("working_hours"))
    court_ids = [court.id for court in selected_courts]
    courts_by_id = {court.id: court for court in selected_courts}

    working_hours = {
        (court.id, hour.weekday): hour
        for court in selected_courts
        for hour in list(court.working_hours.all())
    }
    windows_by_court_date = {}
    demand_buckets = {}
    for court in selected_courts:
        for date_value in daterange(query["date_from"], query["date_to"]):
            window = period_window_for_working_hours(
                date_value,
                working_hours.get((court.id, date_value.weekday())),
                query["period"],
                query.get("hour_from"),
                query.get("hour_to"),
            )
            if window is None:
                continue
            windows_by_court_date[(court.id, date_value)] = window
            bucket_start = fixed_clock_bucket_start(window[0])
            while bucket_start < window[1]:
                bucket_end = bucket_start + timedelta(minutes=DEMAND_BUCKET_MINUTES)
                available = overlap_minutes(
                    bucket_start,
                    bucket_end,
                    window[0],
                    window[1],
                )
                if not available:
                    bucket_start = bucket_end
                    continue
                key = (
                    bucket_start.time().replace(second=0, microsecond=0),
                    bucket_end.time().replace(second=0, microsecond=0),
                )
                demand_buckets.setdefault(key, empty_metrics())[
                    "available_minutes"
                ] += available
                bucket_start = bucket_end

    queryset = (
        Booking.objects.filter(
            court_id__in=court_ids,
            status__in=query["included_statuses"],
            start_time__lt=query["range_end"],
            end_time__gt=query["range_start"],
        )
        .select_related("court", "created_by")
        .annotate(
            paid_amount=Coalesce(
                Sum(
                    "transactions__amount",
                    filter=Q(transactions__is_cancelled=False),
                ),
                Value(ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .only(
            "id",
            "court_id",
            "court__name",
            "start_time",
            "end_time",
            "status",
            "total_price",
            "created_by_id",
            "created_by__first_name",
            "created_by__last_name",
            "created_by__username",
        )
        .order_by("start_time", "id")
    )
    if query.get("staff") is not None:
        queryset = queryset.filter(created_by=query["staff"])
    bookings = list(queryset)

    summary = empty_metrics()
    by_court = {court.id: empty_metrics() for court in selected_courts}
    by_day = {
        date_value: empty_metrics()
        for date_value in daterange(query["date_from"], query["date_to"])
    }
    by_period = {}
    period_names = (
        (PERIOD_DAYTIME, PERIOD_EVENING)
        if query["period"] == PERIOD_ALL_DAY
        else (query["period"],)
    )
    for period_name in period_names:
        by_period[period_name] = empty_metrics()
    by_staff = {}

    for metrics in by_court.values():
        metrics["available_minutes"] = 0
    for date_value in by_day:
        by_day[date_value]["available_minutes"] = 0
    for (court_id, date_value), window in windows_by_court_date.items():
        available = int((window[1] - window[0]).total_seconds() // 60)
        summary["available_minutes"] += available
        by_court[court_id]["available_minutes"] += available
        by_day[date_value]["available_minutes"] += available
        if query["period"] == PERIOD_ALL_DAY:
            for period_name in (PERIOD_DAYTIME, PERIOD_EVENING):
                period_window = period_window_for_working_hours(
                    date_value,
                    working_hours.get((court_id, date_value.weekday())),
                    period_name,
                    None,
                    None,
                )
                if period_window:
                    by_period[period_name]["available_minutes"] += int(
                        (period_window[1] - period_window[0]).total_seconds() // 60
                    )
        else:
            by_period[query["period"]]["available_minutes"] += available

    for booking in bookings:
        local_start_date = timezone.localtime(booking.start_time).date()
        current_date = local_start_date
        while current_date <= timezone.localtime(booking.end_time).date():
            window = windows_by_court_date.get((booking.court_id, current_date))
            minutes = (
                overlap_minutes(
                    booking.start_time, booking.end_time, window[0], window[1]
                )
                if window
                else 0
            )
            if minutes:
                add_booking_once(summary, booking)
                add_booking_once(by_court[booking.court_id], booking)
                add_booking_once(
                    by_day[current_date],
                    booking,
                    include_financial=current_date == local_start_date,
                )
                summary["occupied_minutes"] += minutes
                by_court[booking.court_id]["occupied_minutes"] += minutes
                by_day[current_date]["occupied_minutes"] += minutes
                staff_key = booking.created_by_id
                by_staff.setdefault(staff_key, empty_metrics())
                add_booking_once(by_staff[staff_key], booking)
                by_staff[staff_key]["occupied_minutes"] += minutes

                for key, bucket_metrics in demand_buckets.items():
                    bucket_start = aware_combine(current_date, key[0])
                    bucket_end = aware_combine(current_date, key[1])
                    bucket_minutes = overlap_minutes(
                        booking.start_time,
                        booking.end_time,
                        bucket_start,
                        bucket_end,
                    )
                    if bucket_minutes:
                        add_booking_once(bucket_metrics, booking)
                        bucket_metrics["occupied_minutes"] += bucket_minutes

                target_periods = (
                    (PERIOD_DAYTIME, PERIOD_EVENING)
                    if query["period"] == PERIOD_ALL_DAY
                    else (query["period"],)
                )
                for period_name in target_periods:
                    period_window = period_window_for_working_hours(
                        current_date,
                        working_hours.get((booking.court_id, current_date.weekday())),
                        period_name,
                        query.get("hour_from"),
                        query.get("hour_to"),
                    )
                    if period_window:
                        period_minutes = overlap_minutes(
                            booking.start_time,
                            booking.end_time,
                            period_window[0],
                            period_window[1],
                        )
                        if period_minutes:
                            add_booking_once(by_period[period_name], booking)
                            by_period[period_name]["occupied_minutes"] += period_minutes
            current_date += timedelta(days=1)

    context_court = query.get("court")
    staff = query.get("staff")
    peak_hours = sorted(
        (
            {"hour_from": key[0], "hour_to": key[1], **finalize(metrics)}
            for key, metrics in demand_buckets.items()
        ),
        key=lambda item: (
            -item["booking_count"],
            -item["utilization_percentage"],
            item["hour_from"],
        ),
    )[:5]
    low_demand_hours = sorted(
        (
            {"hour_from": key[0], "hour_to": key[1], **finalize(metrics)}
            for key, metrics in demand_buckets.items()
        ),
        key=lambda item: (
            item["booking_count"],
            item["utilization_percentage"],
            item["hour_from"],
        ),
    )[:5]

    return {
        "context": {
            "club_id": access.club.id,
            "club_name": access.club.name,
            "date_from": query["date_from"],
            "date_to": query["date_to"],
            "court": context_court.id if context_court else None,
            "court_name": context_court.name if context_court else None,
            "period": query["period"],
            "hour_from": query.get("hour_from"),
            "hour_to": query.get("hour_to"),
            "staff": staff.id if staff else None,
            "staff_name": user_name(staff) if staff else None,
            "status": query.get("status"),
            "included_statuses": list(query["included_statuses"]),
            "demand_bucket_minutes": DEMAND_BUCKET_MINUTES,
        },
        "summary": finalize(summary),
        "usage_by_court": [
            {
                "court": court_id,
                "court_name": courts_by_id[court_id].name,
                **finalize(metrics),
            }
            for court_id, metrics in by_court.items()
        ],
        "usage_by_day": [
            {"date": date_value, **finalize(metrics)}
            for date_value, metrics in by_day.items()
        ],
        "usage_by_period": [
            {
                "period": period_name,
                "hour_from": (
                    query.get("hour_from") if period_name == PERIOD_CUSTOM else None
                ),
                "hour_to": (
                    query.get("hour_to") if period_name == PERIOD_CUSTOM else None
                ),
                **finalize(metrics),
            }
            for period_name, metrics in by_period.items()
        ],
        "peak_hours": peak_hours,
        "low_demand_hours": low_demand_hours,
        "staff_booking_activity": [
            {
                "staff": staff_id,
                "staff_name": user_name(
                    next(
                        (
                            booking.created_by
                            for booking in bookings
                            if booking.created_by_id == staff_id
                        ),
                        None,
                    )
                ),
                **finalize(metrics),
            }
            for staff_id, metrics in by_staff.items()
        ],
    }
