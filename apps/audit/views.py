from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from apps.audit.filters import AuditLogFilter
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogDetailSerializer, AuditLogListSerializer
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope


@extend_schema_view(
    list=extend_schema(tags=["Audit Logs"], responses=AuditLogListSerializer),
    retrieve=extend_schema(tags=["Audit Logs"], responses=AuditLogDetailSerializer),
)
class AuditLogViewSet(
    SlotyScopedResourceMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Authorization: club via AuditLog.authorization_config (never court).
    WHO: Platform Admin / Owner / Manager via ROLE_PERMISSIONS["AuditLogViewSet"].
    Staff are matrix-denied (HTTP 403). Out-of-club IDs are omitted from the
    scoped queryset (HTTP 404). Search and filters receive this queryset only.
    """

    authorization_model = AuditLog
    authorization_scope = ResourceScope.CLUB
    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditLogFilter
    http_method_names = ("get", "head", "options")

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("-created", "-id")

    def get_serializer_class(self):
        if self.action == "list":
            return AuditLogListSerializer
        return AuditLogDetailSerializer
