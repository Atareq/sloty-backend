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
from django.db.models.functions import Cast, Least
from django.utils import timezone

from apps.bookings.models import Booking, BookingAttempt
from apps.common.search import customer_phone_search_q


def compute_booking_hold_expires_at(booking):
    policy_expires_at = booking.created + timedelta(
        hours=booking.court.internal_hold_expiry_hours
    )
    return min(policy_expires_at, booking.start_time)


def annotate_booking_hold_expires_at(queryset):
    hold_expiry_duration = ExpressionWrapper(
        Cast("court__internal_hold_expiry_hours", IntegerField())
        * Value(timedelta(hours=1)),
        output_field=DurationField(),
    )
    policy_expires_at = ExpressionWrapper(
        F("created") + hold_expiry_duration,
        output_field=DateTimeField(),
    )
    return queryset.annotate(
        hold_expires_at=Least(
            policy_expires_at,
            F("start_time"),
            output_field=DateTimeField(),
        )
    )


def booking_needs_action_q(*, now, include_expired=False):
    warning_end = now + timedelta(minutes=30)
    query = (
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
    if include_expired:
        query |= Q(status=Booking.Status.EXPIRED)
    return query


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
        help_text=(
            "Search customer_name, customer_phone (including Egyptian phone "
            "variants), and notes."
        ),
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

    def _has_explicit_date_context(self):
        data = self.data or {}
        return any(
            data.get(key) not in (None, "") for key in ("date", "date_from", "date_to")
        )

    def filter_needs_action(self, queryset, name, value):
        if not value:
            return queryset
        queryset = self.with_hold_expiry(queryset)
        return queryset.filter(
            booking_needs_action_q(
                now=timezone.now(),
                include_expired=self._has_explicit_date_context(),
            )
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
        cleaned = (value or "").strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            Q(customer_name__icontains=cleaned)
            | customer_phone_search_q("customer_phone", cleaned)
            | Q(notes__icontains=cleaned)
        )

    def filter_upcoming(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            status__in={Booking.Status.HOLD, Booking.Status.CONFIRMED},
            end_time__gt=timezone.now(),
        )


class BookingAttemptFilter(django_filters.FilterSet):
    STATUS_CHOICES = (
        ("ACCEPTED", "Accepted"),
        ("REJECTED", "Rejected"),
        ("DISMISSED", "Dismissed"),
    )

    court = django_filters.NumberFilter(field_name="court_id")
    attempted_by = django_filters.NumberFilter(field_name="attempted_by_id")
    outcome = django_filters.ChoiceFilter(choices=BookingAttempt.Outcome.choices)
    resolution = django_filters.ChoiceFilter(choices=BookingAttempt.Resolution.choices)
    requested_source = django_filters.ChoiceFilter(choices=Booking.Source.choices)
    status = django_filters.ChoiceFilter(choices=STATUS_CHOICES, method="filter_status")
    date = django_filters.DateFilter(method="filter_date")
    date_from = django_filters.IsoDateTimeFilter(method="filter_date_from")
    date_to = django_filters.IsoDateTimeFilter(method="filter_date_to")

    class Meta:
        model = BookingAttempt
        fields = (
            "court",
            "attempted_by",
            "outcome",
            "resolution",
            "requested_source",
            "status",
            "date",
            "date_from",
            "date_to",
        )

    def filter_status(self, queryset, name, value):
        normalized = (value or "").strip().upper()
        if normalized == "ACCEPTED":
            return queryset.filter(
                outcome=BookingAttempt.Outcome.SUCCESS,
            ).exclude(resolution=BookingAttempt.Resolution.DISMISSED)
        if normalized == "REJECTED":
            return queryset.filter(
                outcome=BookingAttempt.Outcome.REJECTED,
            ).exclude(resolution=BookingAttempt.Resolution.DISMISSED)
        if normalized == "DISMISSED":
            return queryset.filter(resolution=BookingAttempt.Resolution.DISMISSED)
        return queryset.none()

    def filter_date(self, queryset, name, value):
        start_of_day, end_of_day = day_bounds(value)
        return queryset.filter(
            requested_start__lt=end_of_day,
            requested_end__gt=start_of_day,
        )

    def filter_date_from(self, queryset, name, value):
        return queryset.filter(requested_end__gt=value)

    def filter_date_to(self, queryset, name, value):
        return queryset.filter(requested_start__lt=value)
