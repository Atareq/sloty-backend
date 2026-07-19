import django_filters

from apps.settlements.models import Settlement


class SettlementFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Settlement.Status.choices)
    court = django_filters.NumberFilter(field_name="court_id")
    period_from = django_filters.IsoDateTimeFilter(method="filter_period_from")
    period_to = django_filters.IsoDateTimeFilter(method="filter_period_to")
    collected_by = django_filters.NumberFilter(field_name="collected_by_id")
    created_by = django_filters.NumberFilter(field_name="created_by_id")
    settled_by = django_filters.NumberFilter(field_name="settled_by_id")

    class Meta:
        model = Settlement
        fields = (
            "status",
            "court",
            "period_from",
            "period_to",
            "collected_by",
            "created_by",
            "settled_by",
        )

    def filter_period_from(self, queryset, name, value):
        return queryset.filter(period_end__gt=value)

    def filter_period_to(self, queryset, name, value):
        return queryset.filter(period_start__lt=value)
