from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageClubCourts, CanManageClubWorkingHours
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.courts.serializers import (
    CourtCreateSerializer,
    CourtDetailSerializer,
    CourtListSerializer,
    CourtUpdateSerializer,
    CourtWeeklyWorkingHoursSerializer,
    CourtWorkingHourSerializer,
)
from apps.courts.services import (
    pricing_configured_for_court,
    replace_weekly_working_hours,
    serialize_weekly_working_hours,
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
            .prefetch_related("working_hours__pricing_periods")
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
        serializer.save(
            club=self.get_club(),
            created_by=self.request.user,
            default_price="0.00",
        )


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
            .prefetch_related("pricing_periods")
            .order_by("court_id", "weekday", "id")
        )

    def reject_individual_write(self):
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="WORKING_HOURS_USE_WEEKLY_ENDPOINT",
            message=_(
                "Working hours and pricing must be updated through the weekly "
                "court schedule endpoint."
            ),
        )

    def create(self, request, *args, **kwargs):
        self.reject_individual_write()

    def partial_update(self, request, *args, **kwargs):
        self.reject_individual_write()


@extend_schema_view(
    list=extend_schema(
        tags=["Courts"],
        responses=CourtWeeklyWorkingHoursSerializer,
    ),
    update=extend_schema(
        tags=["Courts"],
        request=CourtWeeklyWorkingHoursSerializer,
        responses=CourtWeeklyWorkingHoursSerializer,
    ),
)
class CourtWeeklyWorkingHoursViewSet(ClubScopedAccessMixin, GenericViewSet):
    serializer_class = CourtWeeklyWorkingHoursSerializer
    permission_classes = (CanManageClubWorkingHours,)
    http_method_names = ("get", "put", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Court.objects.none()
        return (
            self.get_access_context()
            .scoped_courts_queryset()
            .prefetch_related("working_hours__pricing_periods")
        )

    def get_court(self):
        access = self.get_access_context()
        court = get_object_or_404(
            self.get_queryset(),
            pk=self.kwargs["court_id"],
            club=access.club,
        )
        if not access.can_access_court(court):
            raise PermissionDenied("You cannot access this court.")
        return court

    def build_response_data(self, court):
        return {
            "court": court.id,
            "court_name": court.name,
            "pricing_configured": pricing_configured_for_court(court),
            "working_hours": serialize_weekly_working_hours(court),
        }

    def list(self, request, *args, **kwargs):
        court = self.get_court()
        return Response(self.build_response_data(court))

    def update(self, request, *args, **kwargs):
        access = self.get_access_context()
        court = self.get_court()
        if not access.can_manage_working_hours(court):
            raise PermissionDenied("You cannot manage working hours for this court.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        replace_weekly_working_hours(
            court=court,
            working_hours=serializer.validated_data["working_hours"],
        )
        court = (
            self.get_access_context()
            .scoped_courts_queryset()
            .prefetch_related("working_hours__pricing_periods")
            .get(pk=court.pk)
        )
        return Response(self.build_response_data(court))
