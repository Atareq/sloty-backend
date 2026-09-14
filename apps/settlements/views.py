from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope
from apps.settlements.authorization import (
    apply_collector_scope,
    can_access_settlement,
    can_manage_settlements,
)
from apps.settlements.filters import SettlementFilter
from apps.settlements.models import Settlement
from apps.settlements.serializers import (
    SettlementCreateSerializer,
    SettlementDetailSerializer,
    SettlementListSerializer,
    SettlementPreviewRequestSerializer,
    SettlementPreviewResponseSerializer,
    SettlementUnsettledSummaryRequestSerializer,
    SettlementUnsettledSummaryResponseSerializer,
)
from apps.settlements.services import mark_settlement_settled


@extend_schema_view(
    list=extend_schema(tags=["Settlements"], responses=SettlementListSerializer),
    create=extend_schema(
        tags=["Settlements"],
        request=SettlementCreateSerializer,
        responses=SettlementDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Settlements"], responses=SettlementDetailSerializer),
)
class SettlementViewSet(
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Authorization: club via Settlement.authorization_config (never court).
    Collector narrowing: actors without can_manage_settlements see only
    collected_by=request.user. Preview/create/summary keep domain authority
    checks in serializers. mark_settled additionally requires management.
    """

    authorization_model = Settlement
    authorization_scope = ResourceScope.CLUB
    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SettlementFilter
    http_method_names = ("get", "post", "head", "options")

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get_authorization_prefetch_related(self):
        if self.action == "retrieve":
            return (
                "lines__transaction__booking",
                "lines__transaction__booking__club_player__player_profile",
                "lines__transaction__court",
            )
        return ()

    def filter_scoped_queryset(self, queryset):
        queryset = apply_collector_scope(self.get_access_context(), queryset)
        return queryset.order_by("-created", "-id")

    def check_object_permission(self, request, obj) -> bool:
        return can_access_settlement(self.access_context, obj)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

    def get_serializer_class(self):
        if self.action == "list":
            return SettlementListSerializer
        if self.action == "create":
            return SettlementCreateSerializer
        return SettlementDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    @extend_schema(
        tags=["Settlements"],
        parameters=[SettlementPreviewRequestSerializer],
        responses=SettlementPreviewResponseSerializer,
        description=(
            "Preview the exact current-custody candidates for one collector. "
            "Candidates are all currently unsettled, non-cancelled signed "
            "transactions in the authorized club, across all payment methods "
            "and all accessible courts unless court is explicitly supplied. "
            "Transaction dates do not limit current custody."
        ),
    )
    @action(detail=False, methods=["get"])
    def preview(self, request, *args, **kwargs):
        serializer = SettlementPreviewRequestSerializer(
            data=request.query_params,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        response_serializer = SettlementPreviewResponseSerializer(serializer.preview())
        return Response(response_serializer.data)

    @extend_schema(
        tags=["Settlements"],
        parameters=[SettlementUnsettledSummaryRequestSerializer],
        responses=SettlementUnsettledSummaryResponseSerializer,
        description=(
            "Read-only current unsettled transaction custody grouped by "
            "collector. Candidate rows match settlement preview: selected "
            "club, is_cancelled=false, no settlement line, collector="
            "Transaction.created_by. period_start is the earliest unsettled "
            "transaction for that collector; period_end is request time. "
            "Transaction dates and payment methods do not limit current "
            "custody. Signed zero and negative totals remain visible. This is "
            "not persisted Settlement history."
        ),
    )
    @action(detail=False, methods=["get"], url_path="unsettled-summary")
    def unsettled_summary(self, request, *args, **kwargs):
        serializer = SettlementUnsettledSummaryRequestSerializer(
            data=request.query_params,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        response_serializer = SettlementUnsettledSummaryResponseSerializer(
            serializer.get_summary()
        )
        return Response(response_serializer.data)

    @extend_schema(
        tags=["Settlements"],
        request=None,
        responses=SettlementDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="mark-settled")
    def mark_settled(self, request, *args, **kwargs):
        if not can_manage_settlements(self.access_context):
            raise PermissionDenied("You cannot manage settlements for this club.")
        settlement = self.get_object()
        settled_settlement = mark_settlement_settled(
            settlement=settlement,
            actor=request.user,
        )
        serializer = SettlementDetailSerializer(
            settled_settlement,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)
