from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageTransactionAttempts, HasClubAccess
from apps.transactions.filters import TransactionAttemptFilter, TransactionFilter
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.serializers import (
    TransactionAttemptDetailSerializer,
    TransactionAttemptDismissSerializer,
    TransactionAttemptListSerializer,
    TransactionCancelSerializer,
    TransactionCreateSerializer,
    TransactionDetailSerializer,
    TransactionListSerializer,
)
from apps.transactions.services import cancel_transaction, dismiss_transaction_attempt


@extend_schema_view(
    list=extend_schema(
        tags=["Transaction Attempts"],
        responses=TransactionAttemptListSerializer,
    ),
    retrieve=extend_schema(
        tags=["Transaction Attempts"],
        responses=TransactionAttemptDetailSerializer,
    ),
)
class TransactionAttemptViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageTransactionAttempts,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TransactionAttemptFilter
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TransactionAttempt.objects.none()
        return (
            self.get_access_context()
            .scoped_transaction_attempts_queryset()
            .select_related(
                "booking",
                "club",
                "court",
                "attempted_by",
                "transaction",
            )
            .order_by("-created", "-id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return TransactionAttemptListSerializer
        if self.action == "dismiss":
            return TransactionAttemptDismissSerializer
        return TransactionAttemptDetailSerializer

    @extend_schema(
        tags=["Transaction Attempts"],
        request=TransactionAttemptDismissSerializer,
        responses=TransactionAttemptDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def dismiss(self, request, *args, **kwargs):
        access = self.get_access_context()
        attempt = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attempt = dismiss_transaction_attempt(
            access=access,
            attempt=attempt,
            actor=request.user,
        )
        return Response(
            TransactionAttemptDetailSerializer(
                attempt,
                context=self.get_serializer_context(),
            ).data
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Transactions"],
        responses=TransactionListSerializer,
    ),
    create=extend_schema(
        tags=["Transactions"],
        request=TransactionCreateSerializer,
        responses=TransactionDetailSerializer,
    ),
    retrieve=extend_schema(
        tags=["Transactions"], responses=TransactionDetailSerializer
    ),
)
class TransactionViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = (HasClubAccess,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TransactionFilter
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        return (
            self.get_access_context()
            .scoped_transactions_queryset()
            .select_related("booking", "club", "court", "created_by", "cancelled_by")
            .order_by("-created", "-id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return TransactionListSerializer
        if self.action == "create":
            return TransactionCreateSerializer
        if self.action == "cancel":
            return TransactionCancelSerializer
        return TransactionDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        response_status = (
            status.HTTP_200_OK
            if getattr(serializer.instance, "_sloty_idempotency_reused", False)
            else status.HTTP_201_CREATED
        )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=response_status, headers=headers)

    @extend_schema(
        tags=["Transactions"],
        request=TransactionCancelSerializer,
        responses=TransactionDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        access = self.get_access_context()
        transaction_obj = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transaction_obj = cancel_transaction(
            access=access,
            transaction_obj=transaction_obj,
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(
            TransactionDetailSerializer(
                transaction_obj,
                context=self.get_serializer_context(),
            ).data
        )
