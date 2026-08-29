from datetime import datetime, time, timedelta

import django_filters
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.common.search import customer_phone_search_q
from apps.transactions.models import Transaction


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = start + timedelta(days=1)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


def make_aware_if_needed(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def parse_filter_bound(value, *, field_name, date_is_end=False):
    raw_value = str(value)
    parsed_date = parse_date(raw_value)
    if parsed_date is not None and "T" not in raw_value and " " not in raw_value:
        start, end = day_bounds(parsed_date)
        return (end if date_is_end else start), True

    parsed_datetime = parse_datetime(raw_value)
    if parsed_datetime is not None:
        return make_aware_if_needed(parsed_datetime), False

    parsed_date = parse_date(raw_value)
    if parsed_date is not None:
        start, end = day_bounds(parsed_date)
        return (end if date_is_end else start), True

    raise serializers.ValidationError({field_name: "Enter a valid date or datetime."})


class TransactionFilter(django_filters.FilterSet):
    SETTLEMENT_STATUS_UNSETTLED = "unsettled"
    SETTLEMENT_STATUS_SETTLED = "settled"

    booking = django_filters.NumberFilter(field_name="booking_id")
    court = django_filters.NumberFilter(field_name="court_id")
    payment_method = django_filters.ChoiceFilter(
        choices=Transaction.PaymentMethod.choices
    )
    transaction_type = django_filters.ChoiceFilter(choices=Transaction.Type.choices)
    date = django_filters.DateFilter(method="filter_date")
    date_from = django_filters.CharFilter(method="filter_date_from")
    date_to = django_filters.CharFilter(method="filter_date_to")
    created_by = django_filters.NumberFilter(field_name="created_by_id")
    is_cancelled = django_filters.BooleanFilter(field_name="is_cancelled")
    settlement_status = django_filters.CharFilter(method="filter_settlement_status")
    settlement = django_filters.NumberFilter(
        field_name="settlement_line__settlement_id"
    )
    search = django_filters.CharFilter(
        method="filter_search",
        help_text=(
            "Search booking customer_name, booking customer_phone "
            "(including Egyptian phone variants), and payment_reference."
        ),
    )
    ordering = django_filters.ChoiceFilter(
        choices=(
            ("created", "created"),
            ("-created", "-created"),
        ),
        method="filter_ordering",
    )

    class Meta:
        model = Transaction
        fields = (
            "booking",
            "court",
            "payment_method",
            "transaction_type",
            "date",
            "date_from",
            "date_to",
            "created_by",
            "is_cancelled",
            "settlement_status",
            "settlement",
            "search",
            "ordering",
        )

    def filter_date(self, queryset, name, value):
        start_of_day, end_of_day = day_bounds(value)
        return queryset.filter(created__gte=start_of_day, created__lt=end_of_day)

    def filter_date_from(self, queryset, name, value):
        start, _date_only = parse_filter_bound(value, field_name="date_from")
        return queryset.filter(created__gte=start)

    def filter_date_to(self, queryset, name, value):
        end, date_only = parse_filter_bound(
            value,
            field_name="date_to",
            date_is_end=True,
        )
        lookup = "created__lt" if date_only else "created__lte"
        return queryset.filter(**{lookup: end})

    def filter_settlement_status(self, queryset, name, value):
        if value in (None, ""):
            return queryset
        if value == self.SETTLEMENT_STATUS_UNSETTLED:
            return queryset.filter(is_cancelled=False, settlement_line__isnull=True)
        if value == self.SETTLEMENT_STATUS_SETTLED:
            return queryset.filter(is_cancelled=False, settlement_line__isnull=False)
        raise serializers.ValidationError(
            {
                "settlement_status": [
                    serializers.ErrorDetail(
                        _("Invalid settlement status value."),
                        code="INVALID_SETTLEMENT_STATUS",
                    )
                ]
            }
        )

    def filter_search(self, queryset, name, value):
        cleaned = (value or "").strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            Q(booking__customer_name__icontains=cleaned)
            | customer_phone_search_q("booking__customer_phone", cleaned)
            | Q(payment_reference__icontains=cleaned)
        )

    def filter_ordering(self, queryset, name, value):
        if value == "created":
            return queryset.order_by("created", "id")
        return queryset.order_by("-created", "-id")
