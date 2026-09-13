"""
Players filters.

FilterSet classes must not perform permission checks or membership lookups.
The ViewSet's get_queryset() returns an already-authorized, club-scoped
queryset before any filter is applied.
"""

import django_filters
from django.db.models import Q

from apps.players.models import ClubPlayer, PlayerProfile


class ClubPlayerFilterSet(django_filters.FilterSet):
    """
    Filtering for the ClubPlayer list endpoint.

    search — case-insensitive substring match across display_name,
              player_profile.phone_number, and player_profile.full_name.
    player_number — exact match on the club-internal number.
    is_current_version — restrict to current or historical versions.
    """

    search = django_filters.CharFilter(method="filter_search", label="Search")
    player_number = django_filters.NumberFilter(
        field_name="player_number",
        lookup_expr="exact",
        label="Player number",
    )
    is_current_version = django_filters.BooleanFilter(
        field_name="is_current_version",
        label="Current version only",
    )

    class Meta:
        model = ClubPlayer
        fields = ["player_number", "is_current_version"]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(display_name__icontains=value)
            | Q(player_profile__phone_number__icontains=value)
            | Q(player_profile__full_name__icontains=value)
        )


class PlayerProfileFilterSet(django_filters.FilterSet):
    """
    Search for PlayerProfile list (club-linked profiles only).

    Matches preferred personal name and phone. Club-local names are
    searched via the current club's ClubPlayer versions.
    """

    search = django_filters.CharFilter(method="filter_search", label="Search")

    class Meta:
        model = PlayerProfile
        fields = []

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(full_name__icontains=value)
            | Q(phone_number__icontains=value)
            | Q(club_players__display_name__icontains=value)
        ).distinct()
