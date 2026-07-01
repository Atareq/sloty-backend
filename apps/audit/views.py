from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from apps.audit.filters import AuditLogFilter
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogDetailSerializer, AuditLogListSerializer
from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanViewClubAuditLogs


@extend_schema_view(
    list=extend_schema(tags=["Audit Logs"], responses=AuditLogListSerializer),
    retrieve=extend_schema(tags=["Audit Logs"], responses=AuditLogDetailSerializer),
)
class AuditLogViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (CanViewClubAuditLogs,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditLogFilter
    http_method_names = ("get", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        return (
            self.get_access_context()
            .scoped_audit_logs_queryset()
            .select_related("club", "court", "actor")
            .order_by("-created", "-id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return AuditLogListSerializer
        return AuditLogDetailSerializer
