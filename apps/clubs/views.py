from rest_framework.generics import get_object_or_404
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
from apps.common.authorization.resolver import resolve_global_scope
from apps.profiles.models import Profile


def scoped_clubs_for_profile(profile):
    if profile is None or not profile.is_enabled:
        return Club.objects.none()
    if profile.role == Profile.Role.ADMIN:
        return Club.objects.all()
    if profile.role == Profile.Role.OWNER:
        owner_profile = getattr(profile, "owner_profile", None)
        return owner_profile.clubs.all() if owner_profile else Club.objects.none()
    if profile.role == Profile.Role.STAFF:
        staff_profile = getattr(profile, "staff_profile", None)
        return (
            Club.objects.filter(pk=staff_profile.club_id)
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

    def get_access_context(self):
        context = getattr(self.request, "access_context", None)
        if context is None:
            context = resolve_global_scope(self.request)
        return context

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Club.objects.none()
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return Club.objects.none()
        context = getattr(self.request, "access_context", None)
        profile = (
            context.profile if context is not None else getattr(user, "profile", None)
        )
        return (
            scoped_clubs_for_profile(profile)
            .select_related("created_by")
            .order_by("id")
        )

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup_val = self.kwargs.get(lookup_url_kwarg)
        if lookup_val is not None and str(lookup_val).isdigit():
            filter_kwargs = {"pk": lookup_val}
        else:
            filter_kwargs = {"slug": lookup_val}
        obj = get_object_or_404(queryset, **filter_kwargs)
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_class(self):
        return {
            "list": ClubListSerializer,
            "create": ClubCreateSerializer,
            "partial_update": ClubUpdateSerializer,
            "update": ClubUpdateSerializer,
        }.get(self.action, ClubDetailSerializer)

    def perform_create(self, serializer):
        access = self.get_access_context()
        serializer.save(created_by=access.profile)
