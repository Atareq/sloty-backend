from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageClubSettlements
from apps.settlements.filters import SettlementFilter
from apps.settlements.models import Settlement
from apps.settlements.serializers import (
    SettlementCreateSerializer,
    SettlementDetailSerializer,
    SettlementListSerializer,
    SettlementPreviewRequestSerializer,
    SettlementPreviewResponseSerializer,
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
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageClubSettlements,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SettlementFilter
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Settlement.objects.none()
        queryset = (
            self.get_access_context()
            .scoped_settlements_queryset()
            .select_related("club", "court", "collected_by", "created_by", "settled_by")
            .order_by("-created", "-id")
        )
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                "lines__transaction__booking",
                "lines__transaction__court",
            )
        return queryset

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
        response_status = (
            status.HTTP_200_OK
            if serializer.data.get("dry_run")
            else status.HTTP_201_CREATED
        )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=response_status, headers=headers)

    @extend_schema(
        tags=["Settlements"],
        parameters=[SettlementPreviewRequestSerializer],
        responses=SettlementPreviewResponseSerializer,
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
        request=None,
        responses=SettlementDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="mark-settled")
    def mark_settled(self, request, *args, **kwargs):
        settlement = mark_settlement_settled(
            access=self.get_access_context(),
            settlement=self.get_object(),
            actor=request.user,
        )
        serializer = SettlementDetailSerializer(
            settlement,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)
