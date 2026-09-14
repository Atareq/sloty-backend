from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope
from apps.transactions.authorization import (
    apply_staff_collector_scope,
    can_cancel_transaction,
    can_dismiss_transaction_attempt,
)
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
    SlotyScopedResourceMixin,
    ListModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Authorization: club+court via TransactionAttempt.authorization_config.
    Staff are additionally restricted to attempted_by=request.user.
    Dismiss remains attempter-only for every role.
    """

    authorization_model = TransactionAttempt
    authorization_scope = ResourceScope.COURT
    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TransactionAttemptFilter
    http_method_names = ("get", "post", "head", "options")

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def filter_scoped_queryset(self, queryset):
        queryset = apply_staff_collector_scope(
            self.get_access_context(),
            queryset,
            actor_field="attempted_by",
        )
        return queryset.order_by("-created", "-id")

    def check_object_permission(self, request, obj) -> bool:
        if self.action == "dismiss":
            return can_dismiss_transaction_attempt(self.access_context, obj)
        return True

    def get_serializer_class(self):
        if self.action == "list":
            return TransactionAttemptListSerializer
        if self.action == "dismiss":
            return TransactionAttemptDismissSerializer
        return TransactionAttemptDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

    @extend_schema(
        tags=["Transaction Attempts"],
        request=TransactionAttemptDismissSerializer,
        responses=TransactionAttemptDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def dismiss(self, request, *args, **kwargs):
        access = self.access_context
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
        responses={
            status.HTTP_201_CREATED: TransactionDetailSerializer,
            status.HTTP_200_OK: TransactionDetailSerializer,
        },
    ),
    retrieve=extend_schema(
        tags=["Transactions"], responses=TransactionDetailSerializer
    ),
)
class TransactionViewSet(
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    GenericViewSet,
):
    """
    Authorization: club+court via Transaction.authorization_config.
    Staff are additionally restricted to created_by=request.user.
    Cancel: Platform Admin any in-scope row; others only their own collections.
    """

    authorization_model = Transaction
    authorization_scope = ResourceScope.COURT
    permission_classes = (SlotyBasePermission,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TransactionFilter
    http_method_names = ("get", "post", "head", "options")

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def filter_scoped_queryset(self, queryset):
        queryset = apply_staff_collector_scope(
            self.get_access_context(),
            queryset,
            actor_field="created_by",
        )
        return queryset.order_by("-created", "-id")

    def check_object_permission(self, request, obj) -> bool:
        if self.action == "cancel":
            return can_cancel_transaction(self.access_context, obj)
        return True

    def get_serializer_class(self):
        if self.action == "list":
            return TransactionListSerializer
        if self.action == "create":
            return TransactionCreateSerializer
        if self.action == "cancel":
            return TransactionCancelSerializer
        return TransactionDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

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
        access = self.access_context
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
