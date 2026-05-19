from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageClubCourts, CanManageClubWorkingHours
from apps.courts.serializers import (
    CourtCreateSerializer,
    CourtDetailSerializer,
    CourtListSerializer,
    CourtUpdateSerializer,
    CourtWorkingHourSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["Courts"], responses=CourtListSerializer),
    create=extend_schema(
        tags=["Courts"],
        request=CourtCreateSerializer,
        responses=CourtDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Courts"], responses=CourtDetailSerializer),
    partial_update=extend_schema(
        tags=["Courts"],
        request=CourtUpdateSerializer,
        responses=CourtDetailSerializer,
    ),
)
class CourtViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageClubCourts,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.courts.models import Court

            return Court.objects.none()
        return (
            self.get_access_context()
            .scoped_courts_queryset()
            .select_related("club", "created_by")
            .prefetch_related("working_hours")
            .order_by("id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return CourtListSerializer
        if self.action == "create":
            return CourtCreateSerializer
        if self.action in {"partial_update", "update"}:
            return CourtUpdateSerializer
        return CourtDetailSerializer

    def perform_create(self, serializer):
        serializer.save(club=self.get_club(), created_by=self.request.user)


@extend_schema_view(
    list=extend_schema(tags=["Courts"], responses=CourtWorkingHourSerializer),
    create=extend_schema(
        tags=["Courts"],
        request=CourtWorkingHourSerializer,
        responses=CourtWorkingHourSerializer,
    ),
    retrieve=extend_schema(tags=["Courts"], responses=CourtWorkingHourSerializer),
    partial_update=extend_schema(
        tags=["Courts"],
        request=CourtWorkingHourSerializer,
        responses=CourtWorkingHourSerializer,
    ),
)
class CourtWorkingHourViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    serializer_class = CourtWorkingHourSerializer
    permission_classes = (CanManageClubWorkingHours,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.courts.models import CourtWorkingHour

            return CourtWorkingHour.objects.none()
        return (
            self.get_access_context()
            .scoped_working_hours_queryset()
            .select_related("court", "court__club")
            .order_by("court_id", "weekday", "id")
        )
