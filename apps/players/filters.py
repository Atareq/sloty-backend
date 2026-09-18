"""
Players filters.

FilterSet classes must not perform permission checks or Profile scope lookups.
The ViewSet's get_queryset() returns an already-authorized, club-scoped
queryset before any filter is applied.
"""

import django_filters
from django.db.models import F, Q
from rest_framework.filters import OrderingFilter

from apps.common.search import customer_phone_search_q
from apps.players.models import ClubPlayer, PlayerProfile


class ClubPlayerFilterSet(django_filters.FilterSet):
    """
    Filtering for the ClubPlayer list endpoint.

    search — case-insensitive substring match across display_name,
              player_profile.phone_number (including Egyptian variants),
              and player_profile.full_name.
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
            | customer_phone_search_q("player_profile__phone_number", value)
            | Q(player_profile__full_name__icontains=value)
        )


class ClubPlayerOrderingFilter(OrderingFilter):
    """
    Ordering filter for the ClubPlayer list endpoint.

    Supports ordering by last_used_at (latest booking recency) and created_at
    (authoritative player profile creation timestamp), with nulls sorted last
    for descending last_used_at.
    """

    ordering_param = "order_by"
    ordering_fields = ("last_used_at", "created_at")

    def get_ordering(self, request, queryset, view):
        params = request.query_params.get(
            self.ordering_param
        ) or request.query_params.get("ordering")
        if params:
            fields = [param.strip() for param in params.split(",")]
            ordering = self.remove_invalid_fields(queryset, fields, view, request)
            if ordering:
                return ordering
        return self.get_default_ordering(view)

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if ordering:
            order_expressions = []
            for term in ordering:
                if term == "-last_used_at":
                    order_expressions.append(F("last_used_at").desc(nulls_last=True))
                elif term == "last_used_at":
                    order_expressions.append(F("last_used_at").asc(nulls_last=True))
                elif term == "-created_at":
                    order_expressions.append(F("player_profile__created_at").desc())
                elif term == "created_at":
                    order_expressions.append(F("player_profile__created_at").asc())
                else:
                    order_expressions.append(term)
            if "id" not in ordering and "-id" not in ordering:
                order_expressions.append("id")
            return queryset.order_by(*order_expressions)
        return queryset


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
