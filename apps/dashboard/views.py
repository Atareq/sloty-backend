from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.models import Club
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
    PublicAvailabilityResponseSerializer,
    RevenueQuerySerializer,
    RevenueSummarySerializer,
)
from apps.dashboard.services import (
    get_calendar_items,
    get_court_availability,
    get_court_utilization,
    get_dashboard_overview,
    get_dashboard_summary,
    get_public_court_availability,
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


class PublicCourtAvailabilityAPIView(GenericAPIView):
    permission_classes = (AllowAny,)
    query_serializer_class = AvailabilityQuerySerializer
    response_serializer_class = PublicAvailabilityResponseSerializer

    @extend_schema(
        tags=["Public"],
        parameters=[AvailabilityQuerySerializer],
        responses=PublicAvailabilityResponseSerializer,
        description=(
            "Sanitized public court availability. Exposes only public club/court "
            "identity, date/window, and AVAILABLE/UNAVAILABLE slot state."
        ),
    )
    def get(self, request, *args, **kwargs):
        query_serializer = self.query_serializer_class(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        club = get_object_or_404(Club, slug=kwargs["club_slug"])
        court = get_object_or_404(
            Court.objects.select_related("club"),
            pk=kwargs["court_id"],
            club=club,
        )
        data = get_public_court_availability(
            club=club,
            court=court,
            date=query_serializer.validated_data["date"],
        )
        serializer = self.response_serializer_class(data)
        return Response(serializer.data)


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
        description=(
            "Period booking and transaction activity plus all-time current "
            "custody. Current custody is the signed sum of currently unsettled, "
            "non-cancelled transactions and is not limited by the date range."
        ),
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
        description=(
            "Period analytics plus all-time current custody. Date, payment_method, "
            "and settlement_status filter period activity only; they do not change "
            "current-custody totals or collector rows. An explicit court or "
            "collected_by filter still narrows authorized custody."
        ),
    )
    def get(self, request, *args, **kwargs):
        query = self.validate_query()
        return self.respond(
            get_dashboard_summary(
                access=self.get_access_context(),
                date_from=query["date_from"],
                date_to=query["date_to"],
                court=query.get("court"),
                collected_by=query.get("collected_by"),
                payment_method=query.get("payment_method"),
                settlement_status=query.get("settlement_status"),
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
