from django.db import transaction

from apps.courts.models import CourtWorkingHour


def format_time(value):
    return value.isoformat() if value is not None else None


def default_closed_working_hour(court, weekday):
    return {
        "id": None,
        "court": court.id,
        "weekday": weekday,
        "opens_at": None,
        "closes_at": None,
        "is_closed": True,
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
            }
        )
    return rows


def replace_weekly_working_hours(*, court, working_hours):
    by_weekday = {row["weekday"]: row for row in working_hours}
    saved = []
    with transaction.atomic():
        for weekday in CourtWorkingHour.Weekday.values:
            row = by_weekday.get(
                weekday,
                {
                    "weekday": weekday,
                    "opens_at": None,
                    "closes_at": None,
                    "is_closed": True,
                },
            )
            working_hour, _created = CourtWorkingHour.objects.update_or_create(
                court=court,
                weekday=weekday,
                defaults={
                    "opens_at": row.get("opens_at"),
                    "closes_at": row.get("closes_at"),
                    "is_closed": row.get("is_closed", False),
                },
            )
            saved.append(working_hour)
    return saved
