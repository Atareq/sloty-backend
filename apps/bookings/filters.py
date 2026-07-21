from datetime import datetime, time, timedelta

import django_filters
from django.db.models import (
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Value,
)
from django.utils import timezone

from apps.bookings.models import Booking


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
        method="filter_remaining_amount_gt"
    )
    ended = django_filters.BooleanFilter(method="filter_ended")
    hold_expiring = django_filters.BooleanFilter(method="filter_hold_expiring")

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
            "ended",
            "hold_expiring",
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

    def filter_ended(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(end_time__lt=timezone.now())

    def filter_hold_expiring(self, queryset, name, value):
        if not value:
            return queryset
        return self.with_hold_expiry(queryset).filter(self.expiring_hold_query())
