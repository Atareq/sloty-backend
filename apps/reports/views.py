from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanViewClubReports
from apps.reports.serializers import (
    CourtUsageReportQuerySerializer,
    CourtUsageReportResponseSerializer,
)
from apps.reports.services import get_court_usage_report


class CourtUsageReportAPIView(ClubScopedAccessMixin, GenericAPIView):
    permission_classes = (CanViewClubReports,)
    query_serializer_class = CourtUsageReportQuerySerializer
    response_serializer_class = CourtUsageReportResponseSerializer

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
