from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.viewsets import GenericViewSet

from apps.clubs.models import Club
from apps.clubs.permissions import CanManageClubs
from apps.clubs.serializers import (
    ClubCreateSerializer,
    ClubDetailSerializer,
    ClubListSerializer,
    ClubUpdateSerializer,
)
from apps.profiles.models import Profile


def scoped_clubs_for_user(user):
    if not user or not user.is_authenticated:
        return Club.objects.none()
    profile = getattr(user, "profile", None)
    if profile is None:
        return Club.objects.none()
    if profile.role == Profile.Role.ADMIN:
        return Club.objects.all()
    if profile.role == Profile.Role.OWNER:
        owner_profile = getattr(profile, "owner_profile", None)
        return owner_profile.clubs.all() if owner_profile else Club.objects.none()
    if profile.role == Profile.Role.STAFF:
        staff_profile = getattr(profile, "staff_profile", None)
        return (
            Club.objects.filter(pk=staff_profile.court.club_id)
            if staff_profile
            else Club.objects.none()
        )
    return Club.objects.none()


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
        return {
            "list": ClubListSerializer,
            "create": ClubCreateSerializer,
            "partial_update": ClubUpdateSerializer,
            "update": ClubUpdateSerializer,
        }.get(self.action, ClubDetailSerializer)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
