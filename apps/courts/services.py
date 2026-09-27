from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status

from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.courts.pricing import (
    MIDNIGHT_END_MINUTES,
    is_valid_period_bounds,
    normalized_pricing_periods,
)


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
    pricing_periods = row.get("pricing_periods", [])

    if not pricing_periods:
        return

    normalized_periods = normalized_pricing_periods(pricing_periods)
    day_start = normalized_periods[0][1]
    previous_end = day_start
    for period, start, end in normalized_periods:
        starts_at = period["starts_at"]
        ends_at = period["ends_at"]
        price = period["price"]
        if not is_valid_period_bounds(starts_at, ends_at):
            raise coded_error(
                "pricing_periods",
                _("Pricing-period start and end times must differ."),
                "WORKING_HOUR_PRICING_INCOMPLETE",
            )
        if price < 0:
            raise coded_error(
                "pricing_periods",
                _("Price must be greater than or equal to zero."),
                "INVALID_WORKING_HOUR_PRICE",
            )
        if (start - day_start) % court.slot_duration_minutes or (
            end - day_start
        ) % court.slot_duration_minutes:
            raise coded_error(
                "pricing_periods",
                _("Pricing-period boundaries must align with the court slot duration."),
                "PRICING_PERIOD_NOT_ALIGNED_WITH_SLOT_DURATION",
            )
        if start < previous_end:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods must not overlap."),
                "WORKING_HOUR_PRICING_OVERLAP",
            )
        # Gaps are closed time. Only a period carried past midnight must
        # continue the timeline; otherwise it would silently move to the
        # next calendar day.
        if start != previous_end and start >= MIDNIGHT_END_MINUTES:
            raise coded_error(
                "pricing_periods",
                _("Pricing periods after midnight must continue without gaps."),
                "WORKING_HOUR_PRICING_INCOMPLETE",
            )
        previous_end = end


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


def lowest_working_hour_price_for_court(court):
    """
    Return the minimum CourtWorkingHourPricePeriod.price for ``court``, or
    None when the court has no configured working-hour prices yet.
    """
    if getattr(court, "pk", None) is None:
        return None
    cached = getattr(court, "_prefetched_objects_cache", None)
    if cached is not None and "working_hours" in cached:
        return get_court_pricing_summary(court)["minimum_slot_price"]
    return (
        CourtWorkingHourPricePeriod.objects.filter(working_hour__court_id=court.pk)
        .order_by("price")
        .values_list("price", flat=True)
        .first()
    )


def lowest_price_from_weekly_payload(working_hours):
    prices = [
        period["price"]
        for row in working_hours
        for period in row.get("pricing_periods", [])
    ]
    return min(prices) if prices else None


def minimum_deposit_above_lowest_price_message(lowest_price) -> str:
    price_val = (
        int(lowest_price) if lowest_price == int(lowest_price) else float(lowest_price)
    )
    return (
        f"الحد الأدنى للعربون لا يمكن أن يزيد عن {price_val} ج.م، "
        "لأنه أقل سعر حجز للملعب."
    )


def validate_minimum_deposit_against_lowest_price(
    *,
    minimum_deposit,
    lowest_price,
    field="minimum_deposit",
):
    """
    Enforce: 0 <= minimum_deposit <= MIN(configured working-hour prices).

    When ``lowest_price`` is None (no pricing configured yet), the invariant
    is not enforceable and validation is a no-op.
    """
    if lowest_price is None:
        return
    if minimum_deposit > lowest_price:
        price_val = (
            int(lowest_price)
            if lowest_price == int(lowest_price)
            else float(lowest_price)
        )
        raise SlotyAPIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="MINIMUM_DEPOSIT_ABOVE_LOWEST_WORKING_HOUR_PRICE",
            message=minimum_deposit_above_lowest_price_message(lowest_price),
            details={
                "lowest_working_hour_price": price_val,
                "currency": "EGP",
            },
        )


def validate_slot_duration_against_pricing(court, slot_duration_minutes):
    for working_hour in court.working_hours.all():
        periods = list(working_hour.pricing_periods.all())
        if not periods:
            continue
        normalized_periods = normalized_pricing_periods(periods)
        day_start = normalized_periods[0][1]
        for _period, start, end in normalized_periods:
            if (start - day_start) % slot_duration_minutes or (
                end - day_start
            ) % slot_duration_minutes:
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
        validate_minimum_deposit_against_lowest_price(
            minimum_deposit=locked_court.minimum_deposit,
            lowest_price=lowest_price_from_weekly_payload(working_hours),
            field="pricing_periods",
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
