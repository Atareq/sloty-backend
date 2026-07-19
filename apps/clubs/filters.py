import django_filters
from django.db.models import Q

from apps.clubs.models import ClubMembership


class ClubUserFilter(django_filters.FilterSet):
    role = django_filters.ChoiceFilter(choices=ClubMembership.Role.choices)
    court = django_filters.NumberFilter(field_name="court_id")
    is_active = django_filters.BooleanFilter()
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = ClubMembership
        fields = ("role", "court", "is_active", "search")

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__username__icontains=value)
            | Q(user__first_name__icontains=value)
            | Q(user__last_name__icontains=value)
            | Q(user__phone_number__icontains=value)
            | Q(user__email__icontains=value)
        )
