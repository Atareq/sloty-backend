from datetime import datetime, time

import django_filters
from django.utils import timezone

from apps.audit.models import AuditLog


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = datetime.combine(date_value, time.max)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


class AuditLogFilter(django_filters.FilterSet):
    action = django_filters.ChoiceFilter(choices=AuditLog.Action.choices)
    entity_type = django_filters.CharFilter(field_name="entity_type")
    entity_id = django_filters.NumberFilter(field_name="entity_id")
    actor = django_filters.NumberFilter(field_name="actor_id")
    court = django_filters.NumberFilter(field_name="court_id")
    date = django_filters.DateFilter(method="filter_date")
    date_from = django_filters.IsoDateTimeFilter(
        field_name="created",
        lookup_expr="gte",
    )
    date_to = django_filters.IsoDateTimeFilter(
        field_name="created",
        lookup_expr="lte",
    )

    class Meta:
        model = AuditLog
        fields = (
            "action",
            "entity_type",
            "entity_id",
            "actor",
            "court",
            "date",
            "date_from",
            "date_to",
        )

    def filter_date(self, queryset, name, value):
        start_of_day, end_of_day = day_bounds(value)
        return queryset.filter(created__gte=start_of_day, created__lte=end_of_day)
