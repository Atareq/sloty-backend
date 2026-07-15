from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.courts.models import CourtWorkingHour
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction

ZERO = Decimal("0.00")


def money(value):
    return value or ZERO


def money_sum(expression, *, filter=None):
    return Coalesce(
        Sum(expression, filter=filter),
        Value(ZERO),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def datetime_for_date(date_value, time_value):
    naive = datetime.combine(date_value, time_value)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def get_court_availability(*, access, court, date):
    if not access.can_view_court_availability(court):
        raise PermissionDenied("You cannot access availability for this court.")
    if not access.club.is_active:
        raise serializers.ValidationError({"club": "Club is inactive."})
    if not court.is_active:
        raise serializers.ValidationError({"court": "Court is inactive."})

    working_hour = CourtWorkingHour.objects.filter(
        court=court,
        weekday=date.weekday(),
    ).first()
    base_response = {
        "club": {
            "id": access.club.id,
            "slug": access.club.slug,
            "name": access.club.name,
        },
        "court": {
            "id": court.id,
            "name": court.name,
        },
        "date": date,
        "is_closed": True,
        "opens_at": None,
        "closes_at": None,
        "slot_duration_minutes": court.slot_duration_minutes,
        "slots": [],
    }
    if working_hour is None:
        return base_response

    base_response["opens_at"] = working_hour.opens_at
    base_response["closes_at"] = working_hour.closes_at
    if working_hour.is_closed:
        return base_response

    base_response["is_closed"] = False
    opens_at = datetime_for_date(date, working_hour.opens_at)
    closes_at = datetime_for_date(date, working_hour.closes_at)
    slot_delta = timedelta(minutes=court.slot_duration_minutes)

    blocking_bookings = list(
        Booking.objects.filter(
            court=court,
            status__in=Booking.BLOCKING_STATUSES,
            start_time__lt=closes_at,
            end_time__gt=opens_at,
        ).order_by("start_time", "id")
    )

    current = opens_at
    while current + slot_delta <= closes_at:
        slot_end = current + slot_delta
        blocking_booking = next(
            (
                booking
                for booking in blocking_bookings
                if booking.start_time < slot_end and booking.end_time > current
            ),
            None,
        )
        base_response["slots"].append(
            {
                "start_time": current,
                "end_time": slot_end,
                "is_available": blocking_booking is None,
                "blocking_booking": (
                    blocking_booking.id if blocking_booking is not None else None
                ),
                "blocking_status": (
                    blocking_booking.status if blocking_booking is not None else None
                ),
            }
        )
        current = slot_end
    return base_response


def validate_dashboard_access(access):
    if not access.can_view_dashboard():
        raise PermissionDenied("You cannot access dashboard summaries.")


def validate_calendar_access(access):
    if not access.can_view_calendar():
        raise PermissionDenied("You cannot access this calendar.")


def get_calendar_items(*, access, date_from, date_to, court=None, status=None):
    validate_calendar_access(access)
    queryset = (
        access.scoped_calendar_bookings_queryset()
        .select_related("court")
        .filter(start_time__lt=date_to, end_time__gt=date_from)
        .annotate(
            paid_amount=money_sum(
                "transactions__amount",
                filter=Q(transactions__is_cancelled=False),
            ),
        )
        .order_by("start_time", "id")
    )
    if court is not None:
        if not access.can_access_court(court):
            raise PermissionDenied("You cannot access this court.")
        queryset = queryset.filter(court=court)
    if status:
        queryset = queryset.filter(status=status)

    items = []
    for booking in queryset:
        paid_amount = money(booking.paid_amount)
        remaining_amount = booking.total_price - paid_amount
        items.append(
            {
                "id": booking.id,
                "court": booking.court_id,
                "court_name": booking.court.name,
                "title": booking.customer_name,
                "customer_name": booking.customer_name,
                "customer_phone": str(booking.customer_phone),
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "status": booking.status,
                "source": booking.source,
                "total_price": booking.total_price,
                "paid_amount": paid_amount,
                "remaining_amount": remaining_amount,
                "is_fully_paid": paid_amount >= booking.total_price,
            }
        )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
    }


def dashboard_bookings_queryset(*, access, date_from, date_to, court=None):
    queryset = Booking.objects.filter(
        court__in=access.scoped_dashboard_courts_queryset(),
        start_time__gte=date_from,
        start_time__lt=date_to,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


def dashboard_transactions_queryset(*, access, date_from, date_to, court=None):
    queryset = Transaction.objects.filter(
        court__in=access.scoped_dashboard_courts_queryset(),
        created__gte=date_from,
        created__lt=date_to,
        is_cancelled=False,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


def get_dashboard_overview(*, access, date_from, date_to, court=None):
    validate_dashboard_access(access)
    if court is not None and not access.can_access_court(court):
        raise PermissionDenied("You cannot access this court.")

    bookings = dashboard_bookings_queryset(
        access=access,
        date_from=date_from,
        date_to=date_to,
        court=court,
    )
    transactions = dashboard_transactions_queryset(
        access=access,
        date_from=date_from,
        date_to=date_to,
        court=court,
    )
    booking_counts = {status: 0 for status, _label in Booking.Status.choices}
    for row in bookings.values("status").annotate(total=Count("id")):
        booking_counts[row["status"]] = row["total"]

    booking_value = money(bookings.aggregate(total=Sum("total_price"))["total"])
    booking_paid = money(
        Transaction.objects.filter(
            booking__in=bookings.values("id"),
            is_cancelled=False,
        ).aggregate(total=Sum("amount"))["total"]
    )
    transaction_summary = transactions.aggregate(
        transaction_total=money_sum("amount"),
        transaction_count=Count("id"),
        unsettled_transaction_amount=money_sum(
            "amount",
            filter=Q(settlement_line__isnull=True),
        ),
        unsettled_transaction_count=Count(
            "id",
            filter=Q(settlement_line__isnull=True),
        ),
        settled_amount=money_sum(
            "amount",
            filter=Q(settlement_line__isnull=False),
        ),
        settled_transaction_count=Count(
            "id",
            filter=Q(settlement_line__isnull=False),
        ),
    )

    settlements = Settlement.objects.filter(club=access.club)
    if court is not None:
        settlements = settlements.filter(court=court)
    pending_settlements = settlements.filter(
        status=Settlement.Status.PENDING,
        created__gte=date_from,
        created__lt=date_to,
    )
    settled_settlements = settlements.filter(status=Settlement.Status.SETTLED).filter(
        Q(settled_at__gte=date_from, settled_at__lt=date_to)
        | Q(settled_at__isnull=True, created__gte=date_from, created__lt=date_to)
    )
    pending_summary = pending_settlements.aggregate(
        amount=money_sum("total_amount"),
        count=Count("id"),
    )
    settled_summary = settled_settlements.aggregate(
        amount=money_sum("total_amount"),
        count=Count("id"),
    )

    courts = access.scoped_dashboard_courts_queryset()
    if court is not None:
        courts = courts.filter(id=court.id)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "court": court.id if court else None,
        "booking_counts_by_status": booking_counts,
        "total_bookings": sum(booking_counts.values()),
        "total_booking_value": booking_value,
        "total_paid_amount": booking_paid,
        "total_remaining_amount": booking_value - booking_paid,
        "transaction_total": transaction_summary["transaction_total"],
        "transaction_count": transaction_summary["transaction_count"],
        "unsettled_transaction_amount": transaction_summary[
            "unsettled_transaction_amount"
        ],
        "unsettled_transaction_count": transaction_summary[
            "unsettled_transaction_count"
        ],
        "settled_amount": transaction_summary["settled_amount"],
        "settled_transaction_count": transaction_summary["settled_transaction_count"],
        "pending_settlement_amount": pending_summary["amount"],
        "pending_settlement_count": pending_summary["count"],
        "settled_settlement_amount": settled_summary["amount"],
        "settled_settlement_count": settled_summary["count"],
        "court_count": courts.count(),
        "active_court_count": courts.filter(is_active=True).count(),
    }


def period_label(value):
    if hasattr(value, "date"):
        return value.date().isoformat()
    return value.isoformat()


def trunc_for_group(group_by):
    if group_by == "week":
        return TruncWeek("created")
    if group_by == "month":
        return TruncMonth("created")
    return TruncDate("created")


def get_revenue_summary(
    *,
    access,
    date_from,
    date_to,
    group_by="day",
    court=None,
    payment_method=None,
):
    validate_dashboard_access(access)
    if court is not None and not access.can_access_court(court):
        raise PermissionDenied("You cannot access this court.")

    transactions = dashboard_transactions_queryset(
        access=access,
        date_from=date_from,
        date_to=date_to,
        court=court,
    )
    if payment_method:
        transactions = transactions.filter(payment_method=payment_method)

    rows = (
        transactions.annotate(period=trunc_for_group(group_by))
        .values("period")
        .annotate(
            transaction_total=money_sum("amount"),
            transaction_count=Count("id"),
            settled_amount=money_sum(
                "amount",
                filter=Q(settlement_line__isnull=False),
            ),
            settled_transaction_count=Count(
                "id",
                filter=Q(settlement_line__isnull=False),
            ),
            unsettled_amount=money_sum(
                "amount",
                filter=Q(settlement_line__isnull=True),
            ),
            unsettled_transaction_count=Count(
                "id",
                filter=Q(settlement_line__isnull=True),
            ),
        )
        .order_by("period")
    )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "group_by": group_by,
        "results": [
            {
                "period": period_label(row["period"]),
                "transaction_total": row["transaction_total"],
                "transaction_count": row["transaction_count"],
                "settled_amount": row["settled_amount"],
                "settled_transaction_count": row["settled_transaction_count"],
                "unsettled_amount": row["unsettled_amount"],
                "unsettled_transaction_count": row["unsettled_transaction_count"],
            }
            for row in rows
        ],
    }


def iter_dates(date_from, date_to):
    current = timezone.localtime(date_from).date()
    final = timezone.localtime(date_to - timedelta(microseconds=1)).date()
    while current <= final:
        yield current
        current += timedelta(days=1)


def available_minutes_for_court(court, working_hours_by_weekday, date_from, date_to):
    total = 0
    for date_value in iter_dates(date_from, date_to):
        working_hour = working_hours_by_weekday.get(date_value.weekday())
        if (
            working_hour is None
            or working_hour.is_closed
            or working_hour.opens_at is None
            or working_hour.closes_at is None
        ):
            continue
        opens_at = datetime_for_date(date_value, working_hour.opens_at)
        closes_at = datetime_for_date(date_value, working_hour.closes_at)
        clipped_start = max(opens_at, date_from)
        clipped_end = min(closes_at, date_to)
        if clipped_start < clipped_end:
            total += int((clipped_end - clipped_start).total_seconds() // 60)
    return total


def booked_minutes_for_booking(booking, date_from, date_to):
    start = max(booking.start_time, date_from)
    end = min(booking.end_time, date_to)
    if start >= end:
        return 0
    return int((end - start).total_seconds() // 60)


def get_court_utilization(*, access, date_from, date_to):
    validate_dashboard_access(access)
    courts = list(
        access.scoped_dashboard_courts_queryset()
        .prefetch_related("working_hours")
        .order_by("id")
    )
    bookings_by_court = {court.id: [] for court in courts}
    for booking in Booking.objects.filter(
        court__in=courts,
        status__in=(
            Booking.Status.HOLD,
            Booking.Status.CONFIRMED,
            Booking.Status.COMPLETED,
        ),
        start_time__lt=date_to,
        end_time__gt=date_from,
    ).order_by("court_id", "start_time", "id"):
        bookings_by_court.setdefault(booking.court_id, []).append(booking)

    transaction_totals = {
        row["court_id"]: row["total"]
        for row in Transaction.objects.filter(
            court__in=courts,
            created__gte=date_from,
            created__lt=date_to,
            is_cancelled=False,
        )
        .values("court_id")
        .annotate(total=money_sum("amount"))
    }

    results = []
    for court in courts:
        working_hours_by_weekday = {
            working_hour.weekday: working_hour
            for working_hour in court.working_hours.all()
        }
        available_minutes = available_minutes_for_court(
            court,
            working_hours_by_weekday,
            date_from,
            date_to,
        )
        court_bookings = bookings_by_court.get(court.id, [])
        booked_minutes = sum(
            booked_minutes_for_booking(booking, date_from, date_to)
            for booking in court_bookings
        )
        utilization_percentage = (
            (Decimal(booked_minutes) / Decimal(available_minutes) * Decimal("100"))
            if available_minutes
            else ZERO
        ).quantize(Decimal("0.01"))
        results.append(
            {
                "court": court.id,
                "court_name": court.name,
                "booking_count": len(court_bookings),
                "booked_minutes": booked_minutes,
                "available_minutes": available_minutes,
                "utilization_percentage": utilization_percentage,
                "transaction_total": transaction_totals.get(court.id, ZERO),
            }
        )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "results": results,
    }
