from datetime import datetime, time

import django_filters
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

    class Meta:
        model = Booking
        fields = (
            "court",
            "status",
            "source",
            "date",
            "date_from",
            "date_to",
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
