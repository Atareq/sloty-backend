"""
Players views.

ClubPlayerViewSet — club-scoped CRUD via Authorization Spine v2.
PlayerProfileViewSet — club-membership-gated access to global profiles,
    list restricted to profiles linked to the current club.
"""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.common.authorization.mixins import (
    ClubScopedViewMixin,
    SlotyScopedResourceMixin,
)
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope
from apps.players.filters import ClubPlayerFilterSet
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.serializers import (
    ClubPlayerCreateSerializer,
    ClubPlayerSerializer,
    PlayerProfileCreateSerializer,
    PlayerProfileSerializer,
)
from apps.players.services import (
    find_or_create_player_profile,
    get_or_create_club_player,
)


@extend_schema_view(
    list=extend_schema(tags=["Players"], responses=ClubPlayerSerializer),
    create=extend_schema(
        tags=["Players"],
        request=ClubPlayerCreateSerializer,
        responses=ClubPlayerSerializer,
    ),
    retrieve=extend_schema(tags=["Players"], responses=ClubPlayerSerializer),
)
class ClubPlayerViewSet(
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Club-scoped list/create/retrieve for ClubPlayer records.

    Authorization: ClubPlayer.authorization_config declares default_scope="club".
    scoped_queryset() enforces the club boundary — cross-club row access is
    structurally impossible. SlotyBasePermission + ROLE_PERMISSIONS["ClubPlayerViewSet"]
    gate the create action.

    The PlayerProfile is found-or-created globally by phone_number during
    perform_create; the club FK is injected from the resolved access context.

    No update/partial_update action is exposed. ClubPlayer is an
    append-oriented historical identity record — it is never edited in
    place (see apps/players/models.py ClubPlayer docstring and
    apps/players/AGENTS.md "ClubPlayer Lifecycle (Append-Only)"). Changing a
    player's identity information goes through
    apps.players.services.supersede_club_player() instead, which soft-deletes
    this row and creates a new one. There is no HTTP endpoint wired to that
    service yet in this sprint — see apps/players/AGENTS.md for the deferred
    API design decision.
    """

    authorization_model = ClubPlayer
    authorization_scope = ResourceScope.CLUB
    authorization_select_related = ("player_profile",)
    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    http_method_names = ("get", "post", "head", "options")
    filterset_class = ClubPlayerFilterSet

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("id")

    def get_serializer_class(self):
        if self.action == "create":
            return ClubPlayerCreateSerializer
        return ClubPlayerSerializer

    def perform_create(self, serializer):
        data = serializer.validated_data
        club = self.get_access_context().club

        profile, _created = find_or_create_player_profile(
            phone_number=data["phone_number"],
            full_name=data.get("full_name", ""),
        )
        club_player, player_created = get_or_create_club_player(
            club=club,
            player_profile=profile,
            display_name=data.get("display_name", ""),
            player_number=data.get("player_number"),
        )
        # Store on serializer.instance so to_representation returns the correct object.
        serializer.instance = club_player

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        response_data = serializer.to_representation(serializer.instance)
        headers = self.get_success_headers(response_data)
        return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)


@extend_schema_view(
    list=extend_schema(
        tags=["Players"],
        responses=PlayerProfileSerializer,
    ),
    create=extend_schema(
        tags=["Players"],
        request=PlayerProfileCreateSerializer,
        responses=PlayerProfileSerializer,
    ),
    retrieve=extend_schema(tags=["Players"], responses=PlayerProfileSerializer),
)
class PlayerProfileViewSet(
    ClubScopedViewMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Club-membership-gated access to global PlayerProfile records.

    List is restricted to profiles that have at least one ClubPlayer in the
    current club — clubs cannot browse profiles that have never been registered
    with them. The profile rows themselves are global (one row per phone number),
    but visibility here is club-bounded.

    Create (POST) is a find-or-create by phone number: if the profile already
    exists globally, the existing record is returned with 200. If it is new,
    201 is returned. This is the mechanism for pre-registering a player before
    booking.
    """

    permission_classes = (SlotyBasePermission,)
    http_method_names = ("get", "post", "head", "options")

    def get_serializer_class(self):
        if self.action == "create":
            return PlayerProfileCreateSerializer
        return PlayerProfileSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PlayerProfile.objects.none()
        # Restrict list to profiles linked to the current club.
        # Players that have never been registered at this club are not visible.
        club = self.access_context.club
        return (
            PlayerProfile.objects.filter(club_players__club=club)
            .distinct()
            .order_by("id")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        profile, created = find_or_create_player_profile(
            phone_number=data["phone_number"],
            full_name=data.get("full_name", ""),
        )
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(
            PlayerProfileSerializer(
                profile, context=self.get_serializer_context()
            ).data,
            status=response_status,
        )
