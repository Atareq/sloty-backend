from datetime import datetime, time, timedelta

import django_filters
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.bookings.identity import booking_identity_search_q
from apps.bookings.models import Booking, BookingAttempt


def logically_expired_hold_q(*, now=None):
    """Match stored HOLD snapshots that are expired or have no deadline."""
    now = now or timezone.now()
    return Q(
        status=Booking.Status.HOLD,
    ) & (Q(hold_expires_at__isnull=True) | Q(hold_expires_at__lte=now))


def exclude_logically_expired_holds(queryset, *, now=None):
    """Omit HOLD rows that are still stored as HOLD but no longer occupy a slot.

    Occupancy, overlap, availability, calendar, and utilization use this
    check. Persisted ``EXPIRED`` is written later by ``expire_hold_bookings``.
    """
    now = now or timezone.now()
    return queryset.exclude(logically_expired_hold_q(now=now))


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
    end = datetime.combine(date_value + timedelta(days=1), time.min)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


def parse_range_bound(value, *, is_end=False):
    """Parse list ``date_from`` / ``date_to`` like slot calendar days.

    ``YYYY-MM-DD`` is a local calendar day: ``date_from`` is that midnight,
    ``date_to`` is the next midnight so the last day is inclusive. ISO
    datetimes keep an exact instant and are not shifted to now.
    """
    raw_value = str(value).strip()
    parsed_date = parse_date(raw_value)
    if parsed_date is not None and "T" not in raw_value and " " not in raw_value:
        start, end = day_bounds(parsed_date)
        return end if is_end else start
    parsed_datetime = parse_datetime(raw_value)
    if parsed_datetime is not None:
        if timezone.is_naive(parsed_datetime):
            parsed_datetime = timezone.make_aware(
                parsed_datetime, timezone.get_current_timezone()
            )
        return parsed_datetime
    raise ValidationError("Enter a valid ISO datetime or YYYY-MM-DD date.")


class DateOrAwareDateTimeField(forms.CharField):
    default_error_messages = {
        "invalid": "Enter a valid ISO datetime or YYYY-MM-DD date.",
    }

    def __init__(self, *args, is_end=False, **kwargs):
        self.is_end = is_end
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if value in self.empty_values:
            return None
        try:
            return parse_range_bound(value, is_end=self.is_end)
        except ValidationError as exc:
            raise ValidationError(exc.messages, code="invalid")


class DateOrAwareDateTimeFilter(django_filters.Filter):
    field_class = DateOrAwareDateTimeField


class BookingFilter(django_filters.FilterSet):
    court = django_filters.NumberFilter(field_name="court_id")
    status = django_filters.ChoiceFilter(choices=Booking.Status.choices)
    source = django_filters.ChoiceFilter(choices=Booking.Source.choices)
    date = django_filters.DateFilter(method="filter_date")
    date_from = DateOrAwareDateTimeFilter(
        method="filter_date_from",
        is_end=False,
        help_text=(
            "Range start as YYYY-MM-DD (local midnight) or an ISO datetime. "
            "Not clamped to now; past ranges return past bookings."
        ),
    )
    date_to = DateOrAwareDateTimeFilter(
        method="filter_date_to",
        is_end=True,
        help_text=("Range end as YYYY-MM-DD (inclusive local day) or an ISO datetime."),
    )
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
            "Search ClubPlayer display name, PlayerProfile phone "
            "(including Egyptian phone variants), and notes."
        ),
    )
    upcoming = django_filters.BooleanFilter(
        method="filter_upcoming",
        help_text=(
            "true returns HOLD and CONFIRMED bookings whose end_time is still "
            "in the future, including in-progress bookings. HOLD rows whose "
            "stored hold_expires_at deadline has passed are omitted."
        ),
    )
    ordering = django_filters.ChoiceFilter(
        choices=(
            ("created", "created"),
            ("-created", "-created"),
        ),
        method="filter_ordering",
        help_text=(
            "Order bookings by creation time ('created' for oldest first, "
            "'-created' for newest first)."
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
            "ordering",
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
        return queryset.filter(self.expiring_hold_query())

    def filter_search(self, queryset, name, value):
        cleaned = (value or "").strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            booking_identity_search_q(cleaned) | Q(notes__icontains=cleaned)
        )

    def filter_upcoming(self, queryset, name, value):
        if not value:
            return queryset
        return exclude_logically_expired_holds(queryset).filter(
            status__in={Booking.Status.HOLD, Booking.Status.CONFIRMED},
            end_time__gt=timezone.now(),
        )

    def filter_ordering(self, queryset, name, value):
        if value == "created":
            return queryset.order_by("created", "id")
        if value == "-created":
            return queryset.order_by("-created", "-id")
        return queryset


class BookingAttemptFilter(django_filters.FilterSet):
    STATUS_CHOICES = (
        ("ACCEPTED", "Accepted"),
        ("REJECTED", "Rejected"),
        ("DISMISSED", "Dismissed"),
    )

    court = django_filters.NumberFilter(field_name="court_id")
    attempted_by = django_filters.NumberFilter(
        field_name="attempted_by_id",
        help_text="Filter by the BookingAttempt actor Profile ID.",
    )
    outcome = django_filters.ChoiceFilter(choices=BookingAttempt.Outcome.choices)
    resolution = django_filters.ChoiceFilter(choices=BookingAttempt.Resolution.choices)
    requested_source = django_filters.ChoiceFilter(choices=Booking.Source.choices)
    status = django_filters.ChoiceFilter(choices=STATUS_CHOICES, method="filter_status")
    date = django_filters.DateFilter(method="filter_date")
    date_from = DateOrAwareDateTimeFilter(
        method="filter_date_from",
        is_end=False,
        help_text=(
            "Range start as YYYY-MM-DD (local midnight) or an ISO datetime. "
            "Not clamped to now; past ranges return past attempts."
        ),
    )
    date_to = DateOrAwareDateTimeFilter(
        method="filter_date_to",
        is_end=True,
        help_text=("Range end as YYYY-MM-DD (inclusive local day) or an ISO datetime."),
    )

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
