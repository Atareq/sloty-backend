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

from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope
from apps.common.exceptions import SlotyAPIException
from apps.courts.authorization import can_manage_working_hours
from apps.courts.models import Court, CourtWorkingHour
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
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    """
    Authorization: club boundary + Court.authorization_config (court scope)
    resolve the authorized queryset (Staff limited to assigned court(s);
    Owner/Admin see all club courts). SlotyBasePermission +
    ROLE_PERMISSIONS["CourtViewSet"] gate create/update to Admin/Owner only.
    """

    authorization_model = Court
    authorization_scope = ResourceScope.COURT
    authorization_select_related = ("created_by",)
    authorization_prefetch_related = ("working_hours__pricing_periods",)
    permission_classes = (SlotyBasePermission,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("id")

    def get_serializer_class(self):
        if self.action == "list":
            return CourtListSerializer
        if self.action == "create":
            return CourtCreateSerializer
        if self.action in {"partial_update", "update"}:
            return CourtUpdateSerializer
        return CourtDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

    def perform_create(self, serializer):
        serializer.save(
            club=self.get_access_context().club,
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
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    """
    Deprecated, read-compatible legacy endpoint. Writes always reject with
    WORKING_HOURS_USE_WEEKLY_ENDPOINT. Reuses the CourtWeeklyWorkingHoursViewSet
    matrix entries (same role/action authority) via `permission_view_name`
    rather than duplicating a second set of matrix rows for a deprecated
    endpoint with identical role authority.
    """

    authorization_model = CourtWorkingHour
    authorization_scope = ResourceScope.COURT
    permission_view_name = "CourtWeeklyWorkingHoursViewSet"
    serializer_class = CourtWorkingHourSerializer
    permission_classes = (SlotyBasePermission,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("court_id", "weekday", "id")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

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
class CourtWeeklyWorkingHoursViewSet(SlotyScopedResourceMixin, GenericViewSet):
    """
    Authorization: `court_id` in the URL is resolved and validated (club +
    existence) generically by resolve_club_scope() before permissions run.
    The scoped queryset then enforces Staff assigned-court isolation for the
    same explicitly targeted court. SlotyBasePermission +
    ROLE_PERMISSIONS["CourtWeeklyWorkingHoursViewSet"] gate list to any club
    member and update/create-adjacent actions to Admin/Owner. The
    domain-specific working-hours predicate is checked explicitly via
    apps.courts.authorization.can_manage_working_hours().
    """

    authorization_model = Court
    authorization_scope = ResourceScope.COURT
    authorization_prefetch_related = ("working_hours__pricing_periods",)
    serializer_class = CourtWeeklyWorkingHoursSerializer
    permission_classes = (SlotyBasePermission,)
    http_method_names = ("get", "put", "head", "options")

    def get_court(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["court_id"])

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
        if not can_manage_working_hours(access, court):
            raise PermissionDenied("You cannot manage working hours for this court.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        replace_weekly_working_hours(
            court=court,
            working_hours=serializer.validated_data["working_hours"],
        )
        court = self.get_scoped_queryset().get(pk=court.pk)
        return Response(self.build_response_data(court))
