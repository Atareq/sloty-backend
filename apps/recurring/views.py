from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import HasClubAccess
from apps.recurring.filters import RecurringAgreementFilter
from apps.recurring.models import RecurringAgreement
from apps.recurring.serializers import (
    RecurringAgreementCreateSerializer,
    RecurringAgreementDetailSerializer,
    RecurringAgreementListSerializer,
    RecurringAvailabilityQuerySerializer,
    RecurringCancellationPreviewSerializer,
    RecurringCancelSerializer,
    RecurringRefundDepositSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=["Recurring Agreements"],
        responses=RecurringAgreementListSerializer,
    ),
    create=extend_schema(
        tags=["Recurring Agreements"],
        request=RecurringAgreementCreateSerializer,
        responses=RecurringAgreementDetailSerializer,
    ),
    retrieve=extend_schema(
        tags=["Recurring Agreements"],
        responses=RecurringAgreementDetailSerializer,
    ),
)
class RecurringAgreementViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (HasClubAccess,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = RecurringAgreementFilter
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RecurringAgreement.objects.none()
        access = self.get_access_context()
        if not access.can_view_recurring_agreement():
            return RecurringAgreement.objects.none()
        return (
            access.scoped_recurring_agreements_queryset()
            .select_related(
                "club",
                "court",
                "created_by",
                "deposit_collected_by",
                "cancelled_by",
                "refunded_by",
            )
            .order_by("-created", "-id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return RecurringAgreementListSerializer
        if self.action == "create":
            return RecurringAgreementCreateSerializer
        if self.action == "availability":
            return RecurringAvailabilityQuerySerializer
        if self.action == "cancellation_preview":
            return RecurringCancellationPreviewSerializer
        if self.action == "cancel":
            return RecurringCancelSerializer
        if self.action == "refund_deposit":
            return RecurringRefundDepositSerializer
        return RecurringAgreementDetailSerializer

    @extend_schema(
        tags=["Recurring Agreements"],
        request=RecurringAvailabilityQuerySerializer,
        responses={200: RecurringAvailabilityQuerySerializer},
    )
    @action(detail=False, methods=["get"], url_path="availability")
    def availability(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.build_preview())

    @extend_schema(
        tags=["Recurring Agreements"],
        request=RecurringCancellationPreviewSerializer,
        responses={200: RecurringCancellationPreviewSerializer},
    )
    @action(detail=True, methods=["post"], url_path="cancellation-preview")
    def cancellation_preview(self, request, *args, **kwargs):
        agreement = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.build_preview(agreement))

    @extend_schema(
        tags=["Recurring Agreements"],
        request=RecurringCancelSerializer,
        responses=RecurringAgreementDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, *args, **kwargs):
        agreement = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.save(agreement)
        return Response(
            RecurringAgreementDetailSerializer(
                agreement,
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=["Recurring Agreements"],
        request=RecurringRefundDepositSerializer,
        responses=RecurringAgreementDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="refund-deposit")
    def refund_deposit(self, request, *args, **kwargs):
        agreement = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.save(agreement)
        return Response(
            RecurringAgreementDetailSerializer(
                agreement,
                context=self.get_serializer_context(),
            ).data
        )
