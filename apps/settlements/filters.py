import django_filters

from apps.settlements.models import Settlement


class SettlementFilter(django_filters.FilterSet):
    court = django_filters.NumberFilter(field_name="court_id")
    settled_by = django_filters.NumberFilter(
        field_name="settled_by_id",
        help_text="Filter by the Profile ID of the actor who performed settlement.",
    )
    collected_by = django_filters.NumberFilter(
        field_name="lines__transaction__created_by_id",
        distinct=True,
        help_text="Filter by the collector Profile ID in Transaction.created_by.",
    )
    date_from = django_filters.IsoDateTimeFilter(
        field_name="created", lookup_expr="gte"
    )
    date_to = django_filters.IsoDateTimeFilter(field_name="created", lookup_expr="lte")
    period_from = django_filters.IsoDateTimeFilter(method="filter_period_from")
    period_to = django_filters.IsoDateTimeFilter(method="filter_period_to")

    class Meta:
        model = Settlement
        fields = (
            "court",
            "settled_by",
            "collected_by",
            "date_from",
            "date_to",
            "period_from",
            "period_to",
        )

    def filter_period_from(self, queryset, name, value):
        return queryset.filter(lines__transaction__created__gte=value).distinct()

    def filter_period_to(self, queryset, name, value):
        return queryset.filter(lines__transaction__created__lte=value).distinct()
