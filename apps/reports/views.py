from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.reports.serializers import (
    CourtUsageReportQuerySerializer,
    CourtUsageReportResponseSerializer,
)
from apps.reports.services import get_court_usage_report


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
