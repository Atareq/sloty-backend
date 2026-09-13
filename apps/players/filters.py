"""
Players filters.

FilterSet classes must not perform permission checks or membership lookups.
The ViewSet's get_queryset() returns an already-authorized, club-scoped
queryset before any filter is applied.
"""

import django_filters
from django.db.models import Q

from apps.players.models import ClubPlayer


class ClubPlayerFilterSet(django_filters.FilterSet):
    """
    Filtering for the ClubPlayer list endpoint.

    search — case-insensitive substring match across display_name,
              player_profile.phone_number, and player_profile.full_name.
    player_number — exact match on the club-internal number.
    """

    search = django_filters.CharFilter(method="filter_search", label="Search")
    player_number = django_filters.NumberFilter(
        field_name="player_number",
        lookup_expr="exact",
        label="Player number",
    )

    class Meta:
        model = ClubPlayer
        fields = ["player_number"]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(display_name__icontains=value)
            | Q(player_profile__phone_number__icontains=value)
            | Q(player_profile__full_name__icontains=value)
        )
