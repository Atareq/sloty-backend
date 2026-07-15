from datetime import datetime, time

import django_filters
from django.utils import timezone

from apps.transactions.models import Transaction


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = datetime.combine(date_value, time.max)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


class TransactionFilter(django_filters.FilterSet):
    booking = django_filters.NumberFilter(field_name="booking_id")
    court = django_filters.NumberFilter(field_name="court_id")
    payment_method = django_filters.ChoiceFilter(
        choices=Transaction.PaymentMethod.choices
    )
    date = django_filters.DateFilter(method="filter_date")
    date_from = django_filters.IsoDateTimeFilter(
        field_name="created",
        lookup_expr="gte",
    )
    date_to = django_filters.IsoDateTimeFilter(
        field_name="created",
        lookup_expr="lte",
    )
    created_by = django_filters.NumberFilter(field_name="created_by_id")
    is_cancelled = django_filters.BooleanFilter(field_name="is_cancelled")

    class Meta:
        model = Transaction
        fields = (
            "booking",
            "court",
            "payment_method",
            "date",
            "date_from",
            "date_to",
            "created_by",
            "is_cancelled",
        )

    def filter_date(self, queryset, name, value):
        start_of_day, end_of_day = day_bounds(value)
        return queryset.filter(created__gte=start_of_day, created__lte=end_of_day)
