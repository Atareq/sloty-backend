from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.bookings.filters import (
    annotate_booking_hold_expires_at,
    booking_needs_action_q,
)
from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)
from apps.bookings.models import Booking
from apps.courts.models import CourtWorkingHour
from apps.courts.pricing import datetime_for_local_date, working_hour_bounds
from apps.dashboard.authorization import (
    can_access_court,
    can_view_financial_summary,
    dashboard_summary_role,
    is_staff_collector,
    scoped_dashboard_bookings,
    scoped_dashboard_courts,
    scoped_dashboard_settlements,
    scoped_dashboard_transactions,
)
from apps.settlements.models import Settlement
from apps.settlements.services import (
    aggregate_current_custody,
    build_current_custody_collector_rows,
    current_custody_aggregate_expressions,
    get_current_unsettled_transactions,
)
from apps.transactions.models import Transaction
from apps.transactions.services import annotate_booking_paid_amount

ZERO = Decimal("0.00")


def money(value):
    return value or ZERO


def money_sum(expression, *, filter=None):
    return Coalesce(
        Sum(expression, filter=filter),
        Value(ZERO),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def datetime_for_date(date_value, time_value, *, is_end=False):
    return datetime_for_local_date(date_value, time_value, is_end=is_end)


def build_court_availability_payload(*, club, court, date):
    working_hour = (
        CourtWorkingHour.objects.filter(
            court=court,
            weekday=date.weekday(),
        )
        .prefetch_related("pricing_periods")
        .first()
    )
    base_response = {
        "club": {
            "id": club.id,
            "slug": club.slug,
            "name": club.name,
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

    bounds = working_hour_bounds(working_hour)
    if bounds is None:
        return base_response

    opens_at, closes_at, _pricing_periods = bounds
    base_response["opens_at"] = opens_at
    base_response["closes_at"] = closes_at
    base_response["is_closed"] = False
    opens_at = datetime_for_date(date, opens_at, is_end=False)
    closes_at = datetime_for_date(date, closes_at, is_end=True)
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


def get_court_availability(*, access, court, date):
    if not can_access_court(access, court):
        raise PermissionDenied("You cannot access availability for this court.")
    if not access.club.is_active:
        raise serializers.ValidationError({"club": "Club is inactive."})
    if not court.is_active:
        raise serializers.ValidationError({"court": "Court is inactive."})
    return build_court_availability_payload(
        club=access.club,
        court=court,
        date=date,
    )


def get_public_court_availability(*, club, court, date):
    if not club.is_active:
        raise serializers.ValidationError({"club": "Club is inactive."})
    if not court.is_active:
        raise serializers.ValidationError({"court": "Court is inactive."})

    availability = build_court_availability_payload(
        club=club,
        court=court,
        date=date,
    )
    availability["slots"] = [
        {
            "start_time": slot["start_time"],
            "end_time": slot["end_time"],
            "availability": "AVAILABLE" if slot["is_available"] else "UNAVAILABLE",
        }
        for slot in availability["slots"]
    ]
    return availability


def get_calendar_items(*, access, date_from, date_to, court=None, status=None):
    queryset = annotate_booking_paid_amount(
        scoped_dashboard_bookings(access)
        .select_related("court", "club_player__player_profile")
        .filter(start_time__lt=date_to, end_time__gt=date_from)
    ).order_by("start_time", "id")
    if court is not None:
        if not can_access_court(access, court):
            raise PermissionDenied("You cannot access this court.")
        queryset = queryset.filter(court=court)
    if status:
        queryset = queryset.filter(status=status)

    items = []
    for booking in queryset:
        paid_amount = money(booking.paid_amount)
        remaining_amount = booking.total_price - paid_amount
        customer_name = booking_customer_display_name(booking)
        customer_phone = booking_customer_display_phone(booking)
        items.append(
            {
                "id": booking.id,
                "court": booking.court_id,
                "court_name": booking.court.name,
                "title": customer_name,
                "customer_name": customer_name,
                "customer_phone": customer_phone,
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
    queryset = scoped_dashboard_bookings(access).filter(
        start_time__gte=date_from,
        start_time__lt=date_to,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


def dashboard_transactions_queryset(*, access, date_from, date_to, court=None):
    queryset = scoped_dashboard_transactions(access).filter(
        created__gte=date_from,
        created__lt=date_to,
        is_cancelled=False,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


BOOKING_STATUS_SUMMARY_FIELDS = {
    Booking.Status.HOLD: "hold_bookings",
    Booking.Status.CONFIRMED: "confirmed_bookings",
    Booking.Status.COMPLETED: "completed_bookings",
    Booking.Status.CANCELLED: "cancelled_bookings",
    Booking.Status.NO_SHOW: "no_show_bookings",
    Booking.Status.EXPIRED: "expired_bookings",
}

SUMMARY_FINANCIAL_FIELDS = (
    "total_booking_value",
    "total_paid_amount",
    "total_remaining_amount",
    "transaction_count",
    "booking_payment_total",
    "booking_refund_total",
    "transaction_total",
    "unsettled_transaction_count",
    "unsettled_transaction_total_amount",
    "settled_transaction_count",
    "settled_transaction_amount",
    "staff_with_unsettled_transactions_count",
    "settled_settlement_count",
    "settled_settlement_amount",
)

COURT_FINANCIAL_FIELDS = (
    "total_booking_value",
    "total_paid_amount",
    "total_remaining_amount",
    "transaction_count",
    "transaction_total",
    "unsettled_transaction_count",
    "unsettled_transaction_total_amount",
    "settled_transaction_count",
    "settled_transaction_amount",
)


def base_booking_counts():
    return {
        "total_bookings": 0,
        **{field: 0 for field in BOOKING_STATUS_SUMMARY_FIELDS.values()},
    }


def null_financial_fields(data, fields):
    for field in fields:
        data[field] = None
    return data


def get_user_display_name(user):
    if user is None:
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def local_date(value):
    return timezone.localtime(value).date()


def with_hold_expiry(queryset):
    return annotate_booking_hold_expires_at(queryset)


def apply_transaction_filters(
    queryset,
    *,
    collected_by=None,
    payment_method=None,
    settlement_status=None,
):
    if collected_by is not None:
        queryset = queryset.filter(created_by=collected_by)
    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)
    if settlement_status == "unsettled":
        queryset = queryset.filter(settlement_line__isnull=True)
    elif settlement_status == "settled":
        queryset = queryset.filter(settlement_line__isnull=False)
    return queryset


def aggregate_transaction_metrics(transactions):
    metrics = transactions.aggregate(
        transaction_total=money_sum("amount"),
        transaction_count=Count("id"),
        booking_payment_total=money_sum(
            "amount",
            filter=Q(transaction_type=Transaction.Type.PAYMENT),
        ),
        booking_refund_total=money_sum(
            "amount",
            filter=Q(transaction_type=Transaction.Type.REFUND),
        ),
        settled_transaction_amount=money_sum(
            "amount",
            filter=Q(settlement_line__isnull=False),
        ),
        settled_transaction_count=Count(
            "id",
            filter=Q(settlement_line__isnull=False),
        ),
    )
    metrics["booking_refund_total"] = abs(money(metrics["booking_refund_total"]))
    return metrics


def get_payment_method_totals(transactions):
    totals = {
        payment_method: {
            "amount": ZERO,
            "refund": ZERO,
            "count": 0,
        }
        for payment_method, _label in Transaction.PaymentMethod.choices
    }
    rows = (
        transactions.values("payment_method")
        .annotate(
            net_amount=money_sum("amount"),
            refund=money_sum(
                "amount",
                filter=Q(transaction_type=Transaction.Type.REFUND),
            ),
            count=Count("id"),
        )
        .order_by("payment_method")
    )
    for row in rows:
        totals[row["payment_method"]] = {
            "amount": money(row["net_amount"]),
            "refund": abs(money(row["refund"])),
            "count": row["count"],
        }
    return totals


def get_staff_unsettled_money(unsettled_transactions, *, court=None):
    results = [
        {
            "collected_by": row["collected_by"],
            "collected_by_name": row["collected_by_name"],
            "court": court.id if court else None,
            "court_name": court.name if court else "",
            "total_unsettled_amount": row["net_amount"],
            "unsettled_transaction_count": row["transaction_count"],
            "totals_by_payment_method": row["totals_by_payment_method"],
        }
        for row in build_current_custody_collector_rows(unsettled_transactions)
    ]
    return sorted(
        results,
        key=lambda item: (-item["total_unsettled_amount"], item["collected_by"]),
    )


def get_needs_action_breakdown(bookings):
    now = timezone.now()
    warning_end = now + timedelta(minutes=30)
    annotated = with_hold_expiry(
        bookings.annotate(
            paid_amount=money_sum(
                "transactions__amount",
                filter=Q(
                    transactions__is_cancelled=False,
                    transactions__transaction_type=Transaction.Type.PAYMENT,
                ),
            ),
        )
    )
    needs_action_query = booking_needs_action_q(now=now, include_expired=True)
    return {
        "needs_action_count": annotated.filter(needs_action_query).aggregate(
            count=Count("id", distinct=True)
        )["count"],
        "hold_waiting_payment_count": annotated.filter(
            status=Booking.Status.HOLD,
        ).count(),
        "overdue_confirmed_count": annotated.filter(
            status=Booking.Status.CONFIRMED,
            end_time__lt=now,
        ).count(),
        "remaining_after_slot_end_count": annotated.filter(
            status=Booking.Status.CONFIRMED,
            end_time__lt=now,
            paid_amount__lt=F("total_price"),
        ).count(),
        "expiring_hold_count": annotated.filter(
            status=Booking.Status.HOLD,
            hold_expires_at__gt=now,
            hold_expires_at__lte=warning_end,
        ).count(),
    }


def settled_settlements_queryset(
    *,
    access,
    date_from,
    date_to,
    court=None,
    courts=None,
):
    settlements = scoped_dashboard_settlements(access)
    if court is not None:
        settlements = settlements.filter(court=court)
    elif courts is not None:
        settlements = settlements.filter(Q(court__in=courts) | Q(court__isnull=True))

    return settlements.filter(status=Settlement.Status.SETTLED).filter(
        Q(settled_at__gte=date_from, settled_at__lt=date_to)
        | Q(settled_at__isnull=True, created__gte=date_from, created__lt=date_to)
    )


def get_dashboard_summary(
    *,
    access,
    date_from,
    date_to,
    court=None,
    collected_by=None,
    payment_method=None,
    settlement_status=None,
):
    financial_visible = can_view_financial_summary(access)
    courts_queryset = scoped_dashboard_courts(access).order_by("id")
    if court is not None:
        if not can_access_court(access, court):
            raise PermissionDenied("You cannot access this court.")
        courts_queryset = courts_queryset.filter(id=court.id)
    courts = list(courts_queryset)
    court_ids = [court_obj.id for court_obj in courts]

    bookings = Booking.objects.filter(
        court__in=courts,
        start_time__gte=date_from,
        start_time__lt=date_to,
    )
    transactions = Transaction.objects.filter(
        court__in=courts,
        created__gte=date_from,
        created__lt=date_to,
        is_cancelled=False,
    )
    transactions = apply_transaction_filters(
        transactions,
        collected_by=collected_by,
        payment_method=payment_method,
        settlement_status=settlement_status,
    )
    current_custody_transactions = get_current_unsettled_transactions(
        access=access,
        court=court,
        collected_by=collected_by,
    )

    counts_by_court = {court_obj.id: base_booking_counts() for court_obj in courts}
    total_counts = base_booking_counts()
    for row in bookings.values("court_id", "status").annotate(total=Count("id")):
        field = BOOKING_STATUS_SUMMARY_FIELDS[row["status"]]
        court_counts = counts_by_court[row["court_id"]]
        court_counts[field] = row["total"]
        court_counts["total_bookings"] += row["total"]
        total_counts[field] += row["total"]
        total_counts["total_bookings"] += row["total"]

    booking_values = {
        row["court_id"]: money(row["total"])
        for row in bookings.values("court_id").annotate(total=money_sum("total_price"))
    }
    booking_paid = {
        row["booking__court_id"]: money(row["total"])
        for row in (
            Transaction.objects.filter(
                booking__in=bookings.values("id"),
                is_cancelled=False,
                transaction_type=Transaction.Type.PAYMENT,
            )
            .values("booking__court_id")
            .annotate(total=money_sum("amount"))
        )
    }
    transaction_summaries = {
        row["court_id"]: row
        for row in transactions.values("court_id").annotate(
            transaction_total=money_sum("amount"),
            transaction_count=Count("id"),
            settled_transaction_amount=money_sum(
                "amount",
                filter=Q(settlement_line__isnull=False),
            ),
            settled_transaction_count=Count(
                "id",
                filter=Q(settlement_line__isnull=False),
            ),
        )
    }
    current_custody_by_court = {
        row["court_id"]: row
        for row in current_custody_transactions.order_by()
        .values("court_id")
        .annotate(**current_custody_aggregate_expressions())
    }

    court_results = []
    for court_obj in courts:
        court_booking_value = booking_values.get(court_obj.id, ZERO)
        court_paid = booking_paid.get(court_obj.id, ZERO)
        transaction_summary = transaction_summaries.get(court_obj.id, {})
        court_custody = current_custody_by_court.get(court_obj.id, {})
        court_data = {
            "court": court_obj.id,
            "court_name": court_obj.name,
            "is_active": court_obj.is_active,
            **counts_by_court[court_obj.id],
            "total_booking_value": court_booking_value,
            "total_paid_amount": court_paid,
            "total_remaining_amount": court_booking_value - court_paid,
            "transaction_count": transaction_summary.get("transaction_count", 0),
            "transaction_total": transaction_summary.get("transaction_total", ZERO),
            "unsettled_transaction_count": court_custody.get(
                "transaction_count",
                0,
            ),
            "unsettled_transaction_total_amount": court_custody.get(
                "net_amount",
                ZERO,
            ),
            "settled_transaction_count": transaction_summary.get(
                "settled_transaction_count",
                0,
            ),
            "settled_transaction_amount": transaction_summary.get(
                "settled_transaction_amount",
                ZERO,
            ),
        }
        if not financial_visible:
            court_data = null_financial_fields(court_data, COURT_FINANCIAL_FIELDS)
        court_results.append(court_data)

    booking_value = sum(booking_values.values(), ZERO)
    paid_amount = sum(booking_paid.values(), ZERO)
    transaction_summary = aggregate_transaction_metrics(transactions)
    current_custody = aggregate_current_custody(current_custody_transactions)
    settled_settlements = settled_settlements_queryset(
        access=access,
        date_from=date_from,
        date_to=date_to,
        court=court,
        courts=courts,
    )
    settled_summary = settled_settlements.aggregate(
        amount=money_sum("total_amount"),
        count=Count("id"),
    )
    summary = {
        "court_count": len(courts),
        "active_court_count": sum(1 for court_obj in courts if court_obj.is_active),
        **total_counts,
        "total_booking_value": booking_value,
        "total_paid_amount": paid_amount,
        "total_remaining_amount": booking_value - paid_amount,
        "transaction_count": transaction_summary["transaction_count"],
        "booking_payment_total": transaction_summary["booking_payment_total"],
        "booking_refund_total": transaction_summary["booking_refund_total"],
        "transaction_total": transaction_summary["transaction_total"],
        "unsettled_transaction_count": current_custody["transaction_count"],
        "unsettled_transaction_total_amount": current_custody["net_amount"],
        "staff_with_unsettled_transactions_count": current_custody["collector_count"],
        "settled_transaction_count": transaction_summary["settled_transaction_count"],
        "settled_transaction_amount": transaction_summary["settled_transaction_amount"],
        "settled_settlement_count": settled_summary["count"],
        "settled_settlement_amount": settled_summary["amount"],
    }
    needs_action = get_needs_action_breakdown(bookings)
    summary["needs_action_count"] = needs_action["needs_action_count"]
    if not financial_visible:
        summary = null_financial_fields(summary, SUMMARY_FINANCIAL_FIELDS)

    effective_context_court = (
        court
        if court is not None
        else courts[0] if is_staff_collector(access) and len(courts) == 1 else None
    )

    return {
        "club": {
            "id": access.club.id,
            "slug": access.club.slug,
            "name": access.club.name,
        },
        "scope": {
            "role": dashboard_summary_role(access),
            "court": court.id if court else None,
            "court_ids": court_ids,
            "financial_visible": financial_visible,
        },
        "period": {
            "date_from": date_from,
            "date_to": date_to,
        },
        "context": {
            "club_id": access.club.id,
            "club_name": access.club.name,
            "date_from": local_date(date_from),
            "date_to": local_date(date_to - timedelta(microseconds=1)),
            "court": effective_context_court.id if effective_context_court else None,
            "court_name": (
                effective_context_court.name if effective_context_court else None
            ),
            "collected_by": collected_by.id if collected_by else None,
            "collected_by_name": (
                get_user_display_name(collected_by) if collected_by else None
            ),
            "payment_method": payment_method,
            "settlement_status": settlement_status,
        },
        "summary": summary,
        "needs_action_breakdown": {
            key: value
            for key, value in needs_action.items()
            if key != "needs_action_count"
        },
        "payment_method_totals": (
            get_payment_method_totals(transactions) if financial_visible else {}
        ),
        "staff_unsettled_money": (
            get_staff_unsettled_money(current_custody_transactions, court=court)
            if financial_visible
            else []
        ),
        "courts": court_results,
    }


def get_dashboard_overview(*, access, date_from, date_to, court=None):
    if court is not None and not can_access_court(access, court):
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
            transaction_type=Transaction.Type.PAYMENT,
        ).aggregate(total=Sum("amount"))["total"]
    )
    transaction_summary = aggregate_transaction_metrics(transactions)
    current_custody = aggregate_current_custody(
        get_current_unsettled_transactions(
            access=access,
            court=court,
        )
    )
    settled_summary = settled_settlements_queryset(
        access=access,
        date_from=date_from,
        date_to=date_to,
        court=court,
    ).aggregate(
        amount=money_sum("total_amount"),
        count=Count("id"),
    )

    courts = scoped_dashboard_courts(access)
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
        "unsettled_transaction_total_amount": current_custody["net_amount"],
        "unsettled_transaction_count": current_custody["transaction_count"],
        "staff_with_unsettled_transactions_count": current_custody["collector_count"],
        "settled_amount": transaction_summary["settled_transaction_amount"],
        "settled_transaction_count": transaction_summary["settled_transaction_count"],
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
    if court is not None and not can_access_court(access, court):
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
        bounds = working_hour_bounds(working_hour) if working_hour is not None else None
        if bounds is None:
            continue
        opens_at, closes_at, _pricing_periods = bounds
        opens_at = datetime_for_date(date_value, opens_at, is_end=False)
        closes_at = datetime_for_date(date_value, closes_at, is_end=True)
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
    courts = list(
        scoped_dashboard_courts(access)
        .prefetch_related("working_hours__pricing_periods")
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
