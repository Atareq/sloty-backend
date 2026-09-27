from django.shortcuts import get_object_or_404
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.serializers import (
    SlotyErrorResponseSerializer,
    SlotyValidationErrorResponseSerializer,
)
from apps.reports.authorization import scoped_report_courts
from apps.reports.serializers import (
    CourtUsageReportQuerySerializer,
    CourtUsageReportResponseSerializer,
    MonthlyCourtStatisticsQuerySerializer,
    MonthlyCourtStatisticsResponseSerializer,
)
from apps.reports.services import get_court_usage_report, get_monthly_court_statistics


class DirectObjectAutoSchema(AutoSchema):
    """Ensure non-paginated endpoints generate single object schema
    rather than array."""

    def _is_list_view(self, serializer=None) -> bool:
        return False


class MonthlyCourtStatisticsAPIView(ClubScopedViewMixin, GenericAPIView):
    permission_classes = (SlotyBasePermission,)
    permission_view_name = "CourtUsageReportViewSet"
    action = "retrieve"
    pagination_class = None
    schema = DirectObjectAutoSchema()

    @extend_schema(
        tags=["Reports"],
        summary="Monthly Court statistics",
        description=(
            "Returns Backend-authoritative monthly income and booking count for "
            "one authorized Court. Requires JWT authentication and the existing "
            "financial-report role (Platform Admin or Club Owner); Staff receive "
            "403. Club access failures return 403, while a Court outside the "
            "authorized Club/Court queryset returns 404. Missing or malformed "
            "month values return the normalized 400 validation response."
        ),
        parameters=[
            OpenApiParameter(
                name="club_slug",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                required=True,
                description="Slug of the authorized Club tenant.",
            ),
            OpenApiParameter(
                name="court_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the Court inside the authorized Club scope.",
            ),
            MonthlyCourtStatisticsQuerySerializer,
        ],
        responses={
            200: MonthlyCourtStatisticsResponseSerializer,
            400: OpenApiResponse(
                response=SlotyValidationErrorResponseSerializer,
                description="Missing or invalid YYYY-MM month value.",
            ),
            401: OpenApiResponse(
                response=SlotyErrorResponseSerializer,
                description="JWT authentication is missing or invalid.",
            ),
            403: OpenApiResponse(
                response=SlotyErrorResponseSerializer,
                description="Club access or the financial-report role is denied.",
            ),
            404: OpenApiResponse(
                response=SlotyErrorResponseSerializer,
                description="Court is not present in the authorized Court queryset.",
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        query = MonthlyCourtStatisticsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        access = self.access_context
        court = get_object_or_404(
            scoped_report_courts(access),
            pk=kwargs["court_id"],
        )
        data = get_monthly_court_statistics(
            access=access,
            court=court,
            month=query.validated_data["month"],
            month_start=query.month_start,
            month_end=query.month_end,
        )
        return Response(MonthlyCourtStatisticsResponseSerializer(data).data)


class CourtUsageReportAPIView(ClubScopedViewMixin, GenericAPIView):
    """
    Read-model court usage report. There is no Report table, so this
    composes ClubScopedViewMixin + SlotyBasePermission rather than
    SlotyScopedResourceMixin. permission_view_name maps onto the existing
    ROLE_PERMISSIONS["CourtUsageReportViewSet"] entries.
    """

    permission_classes = (SlotyBasePermission,)
    permission_view_name = "CourtUsageReportViewSet"
    action = "list"
    pagination_class = None
    schema = DirectObjectAutoSchema()
    query_serializer_class = CourtUsageReportQuerySerializer
    response_serializer_class = CourtUsageReportResponseSerializer

    def get_access_context(self):
        context = self.access_context
        if context is None:
            self.resolve_access_context(self.request)
            context = self.access_context
        return context

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            access = self.get_access_context()
            context["access_context"] = access
            context["club_access"] = access
        return context

    def validate_query(self):
        serializer = self.query_serializer_class(
            data=self.request.query_params,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    @extend_schema(
        tags=["Reports"],
        parameters=[CourtUsageReportQuerySerializer],
        responses=CourtUsageReportResponseSerializer,
    )
    def get(self, request, *args, **kwargs):
        data = get_court_usage_report(
            access=self.get_access_context(),
            query=self.validate_query(),
        )
        serializer = self.response_serializer_class(data)
        return Response(serializer.data)
