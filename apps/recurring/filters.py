import django_filters

from apps.recurring.models import RecurringAgreement


class RecurringAgreementFilter(django_filters.FilterSet):
    court = django_filters.NumberFilter(field_name="court_id")
    status = django_filters.CharFilter(field_name="status")
    deposit_status = django_filters.CharFilter(field_name="deposit_status")
    weekday = django_filters.NumberFilter(field_name="weekday")
    start_date = django_filters.DateFilter(field_name="start_date")
    start_date_from = django_filters.DateFilter(
        field_name="start_date", lookup_expr="gte"
    )
    start_date_to = django_filters.DateFilter(
        field_name="start_date", lookup_expr="lte"
    )

    class Meta:
        model = RecurringAgreement
        fields = (
            "court",
            "status",
            "deposit_status",
            "weekday",
            "start_date",
            "start_date_from",
            "start_date_to",
        )
