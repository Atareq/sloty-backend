"""
Players views.

ClubPlayerViewSet — club-scoped list/create/retrieve via Authorization Spine v2.
PlayerProfileViewSet — club-membership-gated access to global profiles,
    list restricted to profiles linked to the current club, including that
    club's ClubPlayer versions and the recommended (last-used) version.
"""

from django.db.models import Prefetch
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
from apps.players.filters import ClubPlayerFilterSet, PlayerProfileFilterSet
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.serializers import (
    ClubPlayerCreateSerializer,
    ClubPlayerSerializer,
    PlayerProfileCreateSerializer,
    PlayerProfileWithVersionsSerializer,
)
from apps.players.services import (
    create_club_player_version,
    find_or_create_player_profile,
    get_current_club_player,
    get_or_create_club_player,
    last_used_club_player_id_subquery,
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
    Club-scoped list/create/retrieve for ClubPlayer versions.

    Authorization: ClubPlayer.authorization_config declares default_scope="club".
    scoped_queryset() enforces the club boundary — cross-club row access is
    structurally impossible. SlotyBasePermission + ROLE_PERMISSIONS["ClubPlayerViewSet"]
    gate the create action.

    The PlayerProfile is found-or-created globally by phone_number during
    perform_create; the club FK is injected from the resolved access context.

    No update/partial_update action is exposed. ClubPlayer is a permanent,
    versioned historical record — it is never edited in place. POSTing a
    new display_name/player_number for an existing phone+club records a new
    current version via create_club_player_version() and keeps the previous
    row as historical.
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
        display_name = data.get("display_name", "")
        player_number = data.get("player_number")

        profile, _created = find_or_create_player_profile(
            phone_number=data["phone_number"],
            full_name=data.get("full_name", ""),
        )
        current = get_current_club_player(club, profile)
        if current is None:
            club_player, _player_created = get_or_create_club_player(
                club=club,
                player_profile=profile,
                display_name=display_name,
                player_number=player_number,
            )
        else:
            club_player = create_club_player_version(
                current,
                display_name=display_name,
                player_number=player_number,
            )
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
        responses=PlayerProfileWithVersionsSerializer,
    ),
    create=extend_schema(
        tags=["Players"],
        request=PlayerProfileCreateSerializer,
        responses=PlayerProfileWithVersionsSerializer,
    ),
    retrieve=extend_schema(
        tags=["Players"], responses=PlayerProfileWithVersionsSerializer
    ),
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

    List/retrieve include this club's ClubPlayer versions and the
    recommended (last-used, else current) version id so staff can search
    a person and pick a historical club identity when creating a booking.

    List is restricted to profiles that have at least one ClubPlayer in the
    current club — clubs cannot browse profiles that have never been registered
    with them. The profile rows themselves are global (one row per phone number),
    but visibility here is club-bounded.

    Create (POST) is a find-or-create by phone number: if the profile already
    exists globally, the existing record is returned with 200. If it is new,
    201 is returned. This is the mechanism for pre-registering a player before
    booking. Create does not invent a ClubPlayer version by itself.
    """

    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    http_method_names = ("get", "post", "head", "options")
    filterset_class = PlayerProfileFilterSet

    def get_serializer_class(self):
        if self.action == "create":
            return PlayerProfileCreateSerializer
        return PlayerProfileWithVersionsSerializer

    def _club_player_prefetch(self, club):
        return Prefetch(
            "club_players",
            queryset=ClubPlayer.objects.filter(club=club).order_by(
                "-is_current_version", "-id"
            ),
            to_attr="club_versions",
        )

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PlayerProfile.objects.none()
        club = self.access_context.club
        return (
            PlayerProfile.objects.filter(club_players__club=club)
            .distinct()
            .annotate(last_used_club_player_id=last_used_club_player_id_subquery(club))
            .prefetch_related(self._club_player_prefetch(club))
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
        club = self.access_context.club
        profile = (
            PlayerProfile.objects.filter(pk=profile.pk)
            .annotate(last_used_club_player_id=last_used_club_player_id_subquery(club))
            .prefetch_related(self._club_player_prefetch(club))
            .get()
        )
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(
            PlayerProfileWithVersionsSerializer(
                profile, context=self.get_serializer_context()
            ).data,
            status=response_status,
        )
