from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.profiles.models import OwnerProfile, Profile
from apps.profiles.serializers import (
    OwnerCreateSerializer,
    OwnerDetailSerializer,
    OwnerListSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=["Owners"], responses={200: OwnerListSerializer(many=True)}
    ),
    retrieve=extend_schema(tags=["Owners"], responses={200: OwnerDetailSerializer}),
    create=extend_schema(
        tags=["Owners"],
        request=OwnerCreateSerializer,
        responses={201: OwnerDetailSerializer},
    ),
)
class OwnerViewSet(
    ClubScopedViewMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Platform-level Owner onboarding and management endpoint.

    Governed by SlotyBasePermission and the centralized authorization matrix:
    - Only enabled Platform Admins (ADMIN) may create or list owners.
    - Owner and Staff profiles are denied by default.
    """

    permission_classes = (SlotyBasePermission,)
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OwnerProfile.objects.none()

        context = self.access_context
        if not context or context.profile.role != Profile.Role.ADMIN:
            return OwnerProfile.objects.none()

        return (
            OwnerProfile.objects.select_related("user", "created_by")
            .prefetch_related("clubs")
            .order_by("id")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return OwnerCreateSerializer
        if self.action == "list":
            return OwnerListSerializer
        return OwnerDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
        return context
