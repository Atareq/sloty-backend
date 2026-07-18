from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import (
    CanViewClubDashboard,
    CanViewDashboardSummary,
    HasClubAccess,
)
from apps.courts.models import Court
from apps.dashboard.serializers import (
    AvailabilityQuerySerializer,
    AvailabilityResponseSerializer,
    CalendarQuerySerializer,
    CalendarResponseSerializer,
    CourtUtilizationQuerySerializer,
    CourtUtilizationSerializer,
    DashboardOverviewQuerySerializer,
    DashboardOverviewSerializer,
    DashboardSummaryQuerySerializer,
    DashboardSummaryResponseSerializer,
    RevenueQuerySerializer,
    RevenueSummarySerializer,
)
from apps.dashboard.services import (
    get_calendar_items,
    get_court_availability,
    get_court_utilization,
    get_dashboard_overview,
    get_dashboard_summary,
    get_revenue_summary,
)


class DashboardAPIView(ClubScopedAccessMixin, GenericAPIView):
    def validate_query(self):
        serializer = self.query_serializer_class(
            data=self.request.query_params,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def respond(self, data):
        serializer = self.response_serializer_class(data)
        return Response(serializer.data)


class CourtAvailabilityAPIView(DashboardAPIView):
    permission_classes = (HasClubAccess,)
    query_serializer_class = AvailabilityQuerySerializer
    response_serializer_class = AvailabilityResponseSerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[AvailabilityQuerySerializer],
        responses=AvailabilityResponseSerializer,
    )
    def get(self, request, *args, **kwargs):
        access = self.get_access_context()
        court = get_object_or_404(
            Court.objects.select_related("club"),
            pk=kwargs["court_id"],
            club=access.club,
        )
        query = self.validate_query()
        return self.respond(
            get_court_availability(
                access=access,
                court=court,
                date=query["date"],
            )
        )


class ClubCalendarAPIView(DashboardAPIView):
    permission_classes = (HasClubAccess,)
    query_serializer_class = CalendarQuerySerializer
    response_serializer_class = CalendarResponseSerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[CalendarQuerySerializer],
        responses=CalendarResponseSerializer,
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_calendar_items(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
                court=query.get("court"),
                status=query.get("status"),
            )
        )


class DashboardOverviewAPIView(DashboardAPIView):
    permission_classes = (CanViewClubDashboard,)
    query_serializer_class = DashboardOverviewQuerySerializer
    response_serializer_class = DashboardOverviewSerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[DashboardOverviewQuerySerializer],
        responses=DashboardOverviewSerializer,
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_dashboard_overview(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
                court=query.get("court"),
            )
        )


class DashboardSummaryAPIView(DashboardAPIView):
    permission_classes = (CanViewDashboardSummary,)
    query_serializer_class = DashboardSummaryQuerySerializer
    response_serializer_class = DashboardSummaryResponseSerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[DashboardSummaryQuerySerializer],
        responses=DashboardSummaryResponseSerializer,
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_dashboard_summary(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
                court=query.get("court"),
            )
        )


class DashboardRevenueAPIView(DashboardAPIView):
    permission_classes = (CanViewClubDashboard,)
    query_serializer_class = RevenueQuerySerializer
    response_serializer_class = RevenueSummarySerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[RevenueQuerySerializer],
        responses=RevenueSummarySerializer,
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_revenue_summary(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
                group_by=query.get("group_by", "day"),
                court=query.get("court"),
                payment_method=query.get("payment_method"),
            )
        )


class CourtUtilizationAPIView(DashboardAPIView):
    permission_classes = (CanViewClubDashboard,)
    query_serializer_class = CourtUtilizationQuerySerializer
    response_serializer_class = CourtUtilizationSerializer

    @extend_schema(
        tags=["Dashboard"],
        parameters=[CourtUtilizationQuerySerializer],
        responses=CourtUtilizationSerializer,
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_court_utilization(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
            )
        )
