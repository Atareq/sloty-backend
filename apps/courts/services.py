from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.courts.pricing import is_aligned_to_slot_grid


def format_time(value):
    return value.isoformat() if value is not None else None


def coded_error(field, message, code):
    return serializers.ValidationError(
        {field: [serializers.ErrorDetail(message, code=code)]}
    )


def default_closed_working_hour(court, weekday):
    return {
        "id": None,
        "court": court.id,
        "weekday": weekday,
        "opens_at": None,
        "closes_at": None,
        "is_closed": True,
        "pricing_periods": [],
    }


def serialize_price_period(period):
    return {
        "id": period.id,
        "starts_at": format_time(period.starts_at),
        "ends_at": format_time(period.ends_at),
        "price": f"{period.price:.2f}",
    }


def serialize_weekly_working_hours(court):
    existing_by_weekday = {
        working_hour.weekday: working_hour for working_hour in court.working_hours.all()
    }
    rows = []
    for weekday in CourtWorkingHour.Weekday.values:
        working_hour = existing_by_weekday.get(weekday)
        if working_hour is None:
            rows.append(default_closed_working_hour(court, weekday))
            continue
        rows.append(
            {
                "id": working_hour.id,
                "court": court.id,
                "weekday": working_hour.weekday,
                "opens_at": format_time(working_hour.opens_at),
                "closes_at": format_time(working_hour.closes_at),
                "is_closed": working_hour.is_closed,
                "pricing_periods": [
                    serialize_price_period(period)
                    for period in working_hour.pricing_periods.all()
                ],
            }
        )
    return rows


def validate_pricing_periods_for_row(*, court, row):
    is_closed = row.get("is_closed", False)
    pricing_periods = sorted(
        row.get("pricing_periods", []),
        key=lambda period: (period["starts_at"], period["ends_at"]),
    )

    if is_closed:
        if pricing_periods:
            raise coded_error(
                "pricing_periods",
                _("Closed days cannot contain pricing periods."),
                "CLOSED_DAY_CANNOT_HAVE_PRICING",
            )
        return

    opens_at = row.get("opens_at")
    closes_at = row.get("closes_at")
    if not pricing_periods:
        raise coded_error(
            "pricing_periods",
            _("Open working hours require at least one pricing period."),
            "WORKING_HOUR_PRICING_REQUIRED",
        )

    previous_end = opens_at
    for period in pricing_periods:
        starts_at = period["starts_at"]
        ends_at = period["ends_at"]
        price = period["price"]
        if starts_at >= ends_at:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods must cover the full working period without gaps."),
                "WORKING_HOUR_PRICING_INCOMPLETE",
            )
        if price < 0:
            raise coded_error(
                "pricing_periods",
                _("Price must be greater than or equal to zero."),
                "INVALID_WORKING_HOUR_PRICE",
            )
        if starts_at < opens_at or ends_at > closes_at:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods must be inside the configured working hours."),
                "WORKING_HOUR_PRICING_OUTSIDE_HOURS",
            )
        if not (
            is_aligned_to_slot_grid(
                boundary=starts_at,
                opens_at=opens_at,
                slot_duration_minutes=court.slot_duration_minutes,
            )
            and is_aligned_to_slot_grid(
                boundary=ends_at,
                opens_at=opens_at,
                slot_duration_minutes=court.slot_duration_minutes,
            )
        ):
            raise coded_error(
                "pricing_periods",
                _("Pricing-period boundaries must align with the court slot duration."),
                "PRICING_PERIOD_NOT_ALIGNED_WITH_SLOT_DURATION",
            )
        if starts_at < previous_end:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods must not overlap."),
                "WORKING_HOUR_PRICING_OVERLAP",
            )
        if starts_at != previous_end:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods must cover the full working period without gaps."),
                "WORKING_HOUR_PRICING_INCOMPLETE",
            )
        previous_end = ends_at

    if previous_end != closes_at:
        raise coded_error(
            "pricing_periods",
            _("Pricing periods must cover the full working period without gaps."),
            "WORKING_HOUR_PRICING_INCOMPLETE",
        )


def validate_weekly_working_hours_payload(*, court, working_hours):
    for row in working_hours:
        validate_pricing_periods_for_row(court=court, row=row)


def pricing_configured_for_working_hour(working_hour):
    if (
        working_hour.is_closed
        or working_hour.opens_at is None
        or working_hour.closes_at is None
    ):
        return True
    try:
        validate_pricing_periods_for_row(
            court=working_hour.court,
            row={
                "weekday": working_hour.weekday,
                "opens_at": working_hour.opens_at,
                "closes_at": working_hour.closes_at,
                "is_closed": working_hour.is_closed,
                "pricing_periods": [
                    {
                        "starts_at": period.starts_at,
                        "ends_at": period.ends_at,
                        "price": period.price,
                    }
                    for period in working_hour.pricing_periods.all()
                ],
            },
        )
    except serializers.ValidationError:
        return False
    return True


def pricing_configured_for_court(court):
    open_rows = [
        row
        for row in court.working_hours.all()
        if not row.is_closed and row.opens_at is not None and row.closes_at is not None
    ]
    if not open_rows:
        return False
    return all(pricing_configured_for_working_hour(row) for row in open_rows)


def get_court_pricing_summary(court):
    prices = [
        period.price
        for working_hour in court.working_hours.all()
        if not working_hour.is_closed
        for period in working_hour.pricing_periods.all()
    ]
    return {
        "minimum_slot_price": min(prices) if prices else None,
        "maximum_slot_price": max(prices) if prices else None,
    }


def validate_slot_duration_against_pricing(court, slot_duration_minutes):
    for working_hour in court.working_hours.all():
        if working_hour.is_closed or working_hour.opens_at is None:
            continue
        for period in working_hour.pricing_periods.all():
            if not (
                is_aligned_to_slot_grid(
                    boundary=period.starts_at,
                    opens_at=working_hour.opens_at,
                    slot_duration_minutes=slot_duration_minutes,
                )
                and is_aligned_to_slot_grid(
                    boundary=period.ends_at,
                    opens_at=working_hour.opens_at,
                    slot_duration_minutes=slot_duration_minutes,
                )
            ):
                raise coded_error(
                    "slot_duration_minutes",
                    _(
                        "Existing pricing periods are not compatible "
                        "with this slot duration."
                    ),
                    "SLOT_DURATION_CONFLICTS_WITH_PRICING",
                )


def replace_weekly_working_hours(*, court, working_hours):
    by_weekday = {row["weekday"]: row for row in working_hours}
    saved = []
    with transaction.atomic():
        locked_court = (
            Court.objects.select_for_update()
            .prefetch_related("working_hours__pricing_periods")
            .get(pk=court.pk)
        )
        validate_weekly_working_hours_payload(
            court=locked_court,
            working_hours=working_hours,
        )
        for weekday in CourtWorkingHour.Weekday.values:
            row = by_weekday.get(
                weekday,
                {
                    "weekday": weekday,
                    "opens_at": None,
                    "closes_at": None,
                    "is_closed": True,
                    "pricing_periods": [],
                },
            )
            working_hour, _created = CourtWorkingHour.objects.update_or_create(
                court=locked_court,
                weekday=weekday,
                defaults={
                    "opens_at": row.get("opens_at"),
                    "closes_at": row.get("closes_at"),
                    "is_closed": row.get("is_closed", False),
                },
            )
            saved.append(working_hour)
        CourtWorkingHourPricePeriod.objects.filter(working_hour__in=saved).delete()
        periods = []
        for working_hour in saved:
            row = by_weekday.get(working_hour.weekday, {"pricing_periods": []})
            for period in row.get("pricing_periods", []):
                periods.append(
                    CourtWorkingHourPricePeriod(
                        working_hour=working_hour,
                        starts_at=period["starts_at"],
                        ends_at=period["ends_at"],
                        price=period["price"],
                    )
                )
        CourtWorkingHourPricePeriod.objects.bulk_create(periods)
    return saved
