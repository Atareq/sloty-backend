from datetime import datetime, time, timedelta

import django_filters
from django.db.models import (
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
)
from django.db.models.functions import Cast
from django.utils import timezone

from apps.bookings.models import Booking


def compute_booking_hold_expires_at(booking):
    return booking.created + timedelta(hours=booking.court.internal_hold_expiry_hours)


def annotate_booking_hold_expires_at(queryset):
    hold_expiry_duration = ExpressionWrapper(
        Cast("court__internal_hold_expiry_hours", IntegerField())
        * Value(timedelta(hours=1)),
        output_field=DurationField(),
    )
    return queryset.annotate(
        hold_expires_at=ExpressionWrapper(
            F("created") + hold_expiry_duration,
            output_field=DateTimeField(),
        )
    )


def customer_search_query(value):
    query = (value or "").strip()
    if not query:
        return Q()

    filters = Q(customer_name__icontains=query)
    compact = "".join(query.split())
    variants = {query, compact}
    digits = "".join(character for character in compact if character.isdigit())
    if digits:
        variants.add(digits)
        if compact.startswith("+"):
            variants.add(f"+{digits}")
        if digits.startswith("20"):
            variants.add(f"+{digits}")
            if len(digits) > 2:
                variants.add(f"0{digits[2:]}")
        if digits.startswith("0") and len(digits) >= 10:
            variants.add(f"+20{digits[1:]}")
            variants.add(f"20{digits[1:]}")
    for variant in variants:
        if variant:
            filters |= Q(customer_phone__icontains=variant)
    return filters


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = datetime.combine(date_value, time.max)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


class BookingFilter(django_filters.FilterSet):
    court = django_filters.NumberFilter(field_name="court_id")
    status = django_filters.ChoiceFilter(choices=Booking.Status.choices)
    source = django_filters.ChoiceFilter(choices=Booking.Source.choices)
    date = django_filters.DateFilter(method="filter_date")
    date_from = django_filters.IsoDateTimeFilter(method="filter_date_from")
    date_to = django_filters.IsoDateTimeFilter(method="filter_date_to")
    needs_action = django_filters.BooleanFilter(method="filter_needs_action")
    overdue = django_filters.BooleanFilter(method="filter_overdue")
    remaining_amount_gt = django_filters.NumberFilter(
        method="filter_remaining_amount_gt",
        help_text=(
            "Deprecated. Prefer has_remaining_amount. When present, returns "
            "CONFIRMED bookings with remaining amount greater than zero. The "
            "numeric value is ignored except as a presence flag."
        ),
    )
    has_remaining_amount = django_filters.BooleanFilter(
        method="filter_has_remaining_amount",
        help_text=(
            "Canonical remaining-amount filter. true returns bookings whose "
            "paid amount is less than total_price. false returns fully paid "
            "bookings."
        ),
    )
    ended = django_filters.BooleanFilter(method="filter_ended")
    hold_expiring = django_filters.BooleanFilter(method="filter_hold_expiring")
    search = django_filters.CharFilter(
        method="filter_search",
        help_text="Search customer_name and customer_phone.",
    )
    upcoming = django_filters.BooleanFilter(
        method="filter_upcoming",
        help_text=(
            "true returns HOLD and CONFIRMED bookings whose end_time is still "
            "in the future, including in-progress bookings."
        ),
    )

    class Meta:
        model = Booking
        fields = (
            "court",
            "status",
            "source",
            "date",
            "date_from",
            "date_to",
            "needs_action",
            "overdue",
            "remaining_amount_gt",
            "has_remaining_amount",
            "ended",
            "hold_expiring",
            "search",
            "upcoming",
        )

    def filter_date(self, queryset, name, value):
        start_of_day, end_of_day = day_bounds(value)
        return queryset.filter(
            start_time__lt=end_of_day,
            end_time__gt=start_of_day,
        )

    def filter_date_from(self, queryset, name, value):
        return queryset.filter(end_time__gt=value)

    def filter_date_to(self, queryset, name, value):
        return queryset.filter(start_time__lt=value)

    def with_hold_expiry(self, queryset):
        return annotate_booking_hold_expires_at(queryset)

    def expiring_hold_query(self):
        now = timezone.now()
        warning_end = now + timedelta(minutes=30)
        return Q(
            status=Booking.Status.HOLD,
            hold_expires_at__gt=now,
            hold_expires_at__lte=warning_end,
        )

    def filter_needs_action(self, queryset, name, value):
        if not value:
            return queryset
        queryset = self.with_hold_expiry(queryset)
        now = timezone.now()
        return queryset.filter(
            Q(status=Booking.Status.HOLD)
            | Q(status=Booking.Status.CONFIRMED, end_time__lt=now)
            | Q(
                status=Booking.Status.CONFIRMED,
                end_time__lt=now,
                paid_amount__lt=F("total_price"),
            )
            | self.expiring_hold_query()
        ).distinct()

    def filter_overdue(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(end_time__lt=timezone.now())

    def filter_remaining_amount_gt(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(
            status=Booking.Status.CONFIRMED,
            paid_amount__lt=F("total_price"),
        )

    def filter_has_remaining_amount(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(paid_amount__lt=F("total_price"))
        return queryset.filter(paid_amount__gte=F("total_price"))

    def filter_ended(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(end_time__lt=timezone.now())

    def filter_hold_expiring(self, queryset, name, value):
        if not value:
            return queryset
        return self.with_hold_expiry(queryset).filter(self.expiring_hold_query())

    def filter_search(self, queryset, name, value):
        query = customer_search_query(value)
        if not query:
            return queryset
        return queryset.filter(query)

    def filter_upcoming(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            status__in={Booking.Status.HOLD, Booking.Status.CONFIRMED},
            end_time__gt=timezone.now(),
        )
