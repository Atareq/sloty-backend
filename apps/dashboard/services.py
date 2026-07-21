from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DateTimeField,
    DecimalField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)
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


def validate_dashboard_summary_access(access):
    if not access.can_view_dashboard_summary():
        raise PermissionDenied("You cannot access dashboard summaries.")


def dashboard_summary_role(access):
    if access.is_platform_admin:
        return "PLATFORM_ADMIN"
    if access.is_owner:
        return "OWNER"
    if access.is_manager:
        return "MANAGER"
    if access.is_staff:
        return "STAFF"
    return "NONE"


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
    hold_expiry_duration = ExpressionWrapper(
        F("court__internal_hold_expiry_hours") * Value(timedelta(hours=1)),
        output_field=DurationField(),
    )
    return queryset.annotate(
        hold_expires_at=ExpressionWrapper(
            F("created") + hold_expiry_duration,
            output_field=DateTimeField(),
        )
    )


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
    return transactions.aggregate(
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


def get_unsettled_transactions_queryset(
    *, access, court=None, collected_by=None, payment_method=None
):
    if court is not None and not access.can_access_court(court):
        raise PermissionDenied("You cannot access this court.")
    queryset = access.scoped_transactions_queryset().filter(
        club=access.club,
        amount__gt=0,
        settlement_line__isnull=True,
        is_cancelled=False,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    if collected_by is not None:
        queryset = queryset.filter(created_by=collected_by)
    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)
    return queryset


def get_unsettled_transaction_metrics(
    *,
    access,
    court=None,
    collected_by=None,
    payment_method=None,
):
    queryset = get_unsettled_transactions_queryset(
        access=access,
        court=court,
        collected_by=collected_by,
        payment_method=payment_method,
    )
    return queryset.aggregate(
        unsettled_transaction_count=Count("id"),
        unsettled_transaction_total_amount=money_sum("amount"),
        staff_with_unsettled_transactions_count=Count("created_by", distinct=True),
    )


def get_payment_method_totals(transactions):
    return {
        row["payment_method"]: {
            "amount": money(row["amount"]),
            "count": row["count"],
        }
        for row in transactions.values("payment_method")
        .annotate(amount=money_sum("amount"), count=Count("id"))
        .order_by("payment_method")
    }


def get_staff_unsettled_money(unsettled_transactions):
    payment_rows = (
        unsettled_transactions.values(
            "created_by_id",
            "created_by__first_name",
            "created_by__last_name",
            "created_by__username",
            "court_id",
            "court__name",
            "payment_method",
        )
        .annotate(amount=money_sum("amount"))
        .order_by("created_by_id", "court_id", "payment_method")
    )
    results = {}
    for row in payment_rows:
        key = (row["created_by_id"], row["court_id"])
        item = results.setdefault(
            key,
            {
                "collected_by": row["created_by_id"],
                "collected_by_name": (
                    f"{row['created_by__first_name']} {row['created_by__last_name']}"
                ).strip()
                or row["created_by__username"]
                or "",
                "court": row["court_id"],
                "court_name": row["court__name"],
                "total_unsettled_amount": ZERO,
                "unsettled_transaction_count": 0,
                "totals_by_payment_method": {},
            },
        )
        item["totals_by_payment_method"][row["payment_method"]] = money(row["amount"])
        item["total_unsettled_amount"] += money(row["amount"])

    count_rows = unsettled_transactions.values("created_by_id", "court_id").annotate(
        count=Count("id")
    )
    for row in count_rows:
        item = results.get((row["created_by_id"], row["court_id"]))
        if item is not None:
            item["unsettled_transaction_count"] = row["count"]
    return sorted(
        results.values(),
        key=lambda item: (-item["total_unsettled_amount"], item["collected_by"] or 0),
    )


def get_needs_action_breakdown(bookings):
    now = timezone.now()
    warning_end = now + timedelta(minutes=30)
    annotated = with_hold_expiry(
        bookings.annotate(
            paid_amount=money_sum(
                "transactions__amount",
                filter=Q(transactions__is_cancelled=False),
            ),
        )
    )
    needs_action_query = (
        Q(status=Booking.Status.HOLD)
        | Q(status=Booking.Status.CONFIRMED, end_time__lt=now)
        | Q(
            status=Booking.Status.CONFIRMED,
            end_time__lt=now,
            paid_amount__lt=F("total_price"),
        )
        | Q(
            status=Booking.Status.HOLD,
            hold_expires_at__gt=now,
            hold_expires_at__lte=warning_end,
        )
    )
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
    settlements = Settlement.objects.filter(club=access.club)
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
    validate_dashboard_summary_access(access)

    financial_visible = access.can_view_financial_summary()
    courts_queryset = access.scoped_dashboard_summary_courts_queryset().order_by("id")
    if court is not None:
        if not access.can_access_court(court):
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
            unsettled_transaction_total_amount=money_sum(
                "amount",
                filter=Q(settlement_line__isnull=True),
            ),
            unsettled_transaction_count=Count(
                "id",
                filter=Q(settlement_line__isnull=True),
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
    }

    court_results = []
    for court_obj in courts:
        court_booking_value = booking_values.get(court_obj.id, ZERO)
        court_paid = booking_paid.get(court_obj.id, ZERO)
        transaction_summary = transaction_summaries.get(court_obj.id, {})
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
            "unsettled_transaction_count": transaction_summary.get(
                "unsettled_transaction_count",
                0,
            ),
            "unsettled_transaction_total_amount": transaction_summary.get(
                "unsettled_transaction_total_amount",
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
    unsettled_transaction_metrics = get_unsettled_transaction_metrics(
        access=access,
        court=court,
        collected_by=collected_by,
        payment_method=payment_method,
    )
    unsettled_transactions = get_unsettled_transactions_queryset(
        access=access,
        court=court,
        collected_by=collected_by,
        payment_method=payment_method,
    )
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
        "transaction_total": transaction_summary["transaction_total"],
        "unsettled_transaction_count": unsettled_transaction_metrics[
            "unsettled_transaction_count"
        ],
        "unsettled_transaction_total_amount": unsettled_transaction_metrics[
            "unsettled_transaction_total_amount"
        ],
        "staff_with_unsettled_transactions_count": unsettled_transaction_metrics[
            "staff_with_unsettled_transactions_count"
        ],
        "settled_transaction_count": transaction_summary["settled_transaction_count"],
        "settled_transaction_amount": transaction_summary["settled_transaction_amount"],
        "settled_settlement_count": settled_summary["count"],
        "settled_settlement_amount": settled_summary["amount"],
    }
    needs_action = get_needs_action_breakdown(bookings)
    summary["needs_action_count"] = needs_action["needs_action_count"]
    if not financial_visible:
        summary = null_financial_fields(summary, SUMMARY_FINANCIAL_FIELDS)

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
            "court": court.id if court else None,
            "court_name": court.name if court else None,
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
            get_staff_unsettled_money(unsettled_transactions)
            if financial_visible
            else []
        ),
        "courts": court_results,
    }


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
    transaction_summary = aggregate_transaction_metrics(transactions)
    unsettled_transaction_metrics = get_unsettled_transaction_metrics(
        access=access,
        court=court,
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
        "unsettled_transaction_total_amount": unsettled_transaction_metrics[
            "unsettled_transaction_total_amount"
        ],
        "unsettled_transaction_count": unsettled_transaction_metrics[
            "unsettled_transaction_count"
        ],
        "staff_with_unsettled_transactions_count": unsettled_transaction_metrics[
            "staff_with_unsettled_transactions_count"
        ],
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
