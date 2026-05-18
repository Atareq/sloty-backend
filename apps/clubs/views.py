from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.viewsets import GenericViewSet

from apps.clubs.models import Club, ClubMembership
from apps.clubs.permissions import CanManageClubMemberships, CanManageClubs
from apps.clubs.serializers import (
    ClubCreateSerializer,
    ClubDetailSerializer,
    ClubListSerializer,
    ClubMembershipSerializer,
    ClubUpdateSerializer,
)


def scoped_clubs_for_user(user):
    if not user.is_authenticated:
        return Club.objects.none()
    if user.is_platform_super_admin():
        return Club.objects.all()

    scope_filter = Q()
    if user.is_club_owner():
        scope_filter |= Q(
            memberships__user=user,
            memberships__role=ClubMembership.Role.OWNER,
            memberships__is_active=True,
        )
    if user.is_manager():
        scope_filter |= Q(
            memberships__user=user,
            memberships__role=ClubMembership.Role.MANAGER,
            memberships__is_active=True,
        )
    if user.is_staff_member():
        scope_filter |= Q(
            courts__staff_assignments__user=user,
            courts__staff_assignments__is_active=True,
        )

    if not scope_filter:
        return Club.objects.none()
    return Club.objects.filter(scope_filter).distinct()


@extend_schema_view(
    list=extend_schema(tags=["Clubs"], responses=ClubListSerializer),
    create=extend_schema(
        tags=["Clubs"],
        request=ClubCreateSerializer,
        responses=ClubDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Clubs"], responses=ClubDetailSerializer),
    partial_update=extend_schema(
        tags=["Clubs"],
        request=ClubUpdateSerializer,
        responses=ClubDetailSerializer,
    ),
)
class ClubViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageClubs,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        return (
            scoped_clubs_for_user(self.request.user)
            .select_related("created_by")
            .order_by("id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return ClubListSerializer
        if self.action == "create":
            return ClubCreateSerializer
        if self.action in {"partial_update", "update"}:
            return ClubUpdateSerializer
        return ClubDetailSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema_view(
    list=extend_schema(tags=["Clubs"], responses=ClubMembershipSerializer),
    create=extend_schema(
        tags=["Clubs"],
        request=ClubMembershipSerializer,
        responses=ClubMembershipSerializer,
    ),
    retrieve=extend_schema(tags=["Clubs"], responses=ClubMembershipSerializer),
    partial_update=extend_schema(
        tags=["Clubs"],
        request=ClubMembershipSerializer,
        responses=ClubMembershipSerializer,
    ),
)
class ClubMembershipViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    serializer_class = ClubMembershipSerializer
    permission_classes = (CanManageClubMemberships,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        user = self.request.user
        queryset = ClubMembership.objects.select_related(
            "club",
            "user",
            "created_by",
        ).order_by("id")
        if not user.is_authenticated:
            return queryset.none()
        if user.is_platform_super_admin():
            return queryset
        if user.is_club_owner():
            return queryset.filter(
                club__memberships__user=user,
                club__memberships__role=ClubMembership.Role.OWNER,
                club__memberships__is_active=True,
            ).distinct()
        return queryset.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
