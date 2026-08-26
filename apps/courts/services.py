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
                "pricing_periods": [
                    serialize_price_period(period)
                    for period in working_hour.pricing_periods.all()
                ],
            }
        )
    return rows


def validate_pricing_periods_for_row(*, court, row):
    pricing_periods = sorted(
        row.get("pricing_periods", []),
        key=lambda period: (period["starts_at"], period["ends_at"]),
    )

    if not pricing_periods:
        return

    day_start = pricing_periods[0]["starts_at"]
    previous_end = day_start
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
        if not (
            is_aligned_to_slot_grid(
                boundary=starts_at,
                opens_at=day_start,
                slot_duration_minutes=court.slot_duration_minutes,
            )
            and is_aligned_to_slot_grid(
                boundary=ends_at,
                opens_at=day_start,
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


def validate_weekly_working_hours_payload(*, court, working_hours):
    for row in working_hours:
        validate_pricing_periods_for_row(court=court, row=row)


def pricing_configured_for_working_hour(working_hour):
    pricing_periods = [
        {
            "starts_at": period.starts_at,
            "ends_at": period.ends_at,
            "price": period.price,
        }
        for period in working_hour.pricing_periods.all()
    ]
    if not pricing_periods:
        return True
    try:
        validate_pricing_periods_for_row(
            court=working_hour.court,
            row={
                "weekday": working_hour.weekday,
                "pricing_periods": pricing_periods,
            },
        )
    except serializers.ValidationError:
        return False
    return True


def pricing_configured_for_court(court):
    open_rows = [
        row for row in court.working_hours.all() if list(row.pricing_periods.all())
    ]
    if not open_rows:
        return False
    return all(pricing_configured_for_working_hour(row) for row in open_rows)


def get_court_pricing_summary(court):
    cached = getattr(court, "_sloty_pricing_summary", None)
    if cached is not None:
        return cached
    prices = [
        period.price
        for working_hour in court.working_hours.all()
        for period in working_hour.pricing_periods.all()
    ]
    cached = {
        "minimum_slot_price": min(prices) if prices else None,
        "maximum_slot_price": max(prices) if prices else None,
    }
    court._sloty_pricing_summary = cached
    return cached


def validate_slot_duration_against_pricing(court, slot_duration_minutes):
    for working_hour in court.working_hours.all():
        periods = list(working_hour.pricing_periods.all())
        if not periods:
            continue
        opens_at = periods[0].starts_at
        for period in periods:
            if not (
                is_aligned_to_slot_grid(
                    boundary=period.starts_at,
                    opens_at=opens_at,
                    slot_duration_minutes=slot_duration_minutes,
                )
                and is_aligned_to_slot_grid(
                    boundary=period.ends_at,
                    opens_at=opens_at,
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
                    "pricing_periods": [],
                },
            )
            working_hour, _created = CourtWorkingHour.objects.update_or_create(
                court=locked_court,
                weekday=weekday,
                defaults={},
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
