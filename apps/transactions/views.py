from datetime import datetime, time

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.mixins import CreateModelMixin, ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import HasClubAccess
from apps.transactions.models import Transaction
from apps.transactions.serializers import (
    TransactionCreateSerializer,
    TransactionDetailSerializer,
    TransactionListSerializer,
)


def parse_date_param(value, field_name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise serializers.ValidationError({field_name: "Use YYYY-MM-DD format."})


def parse_datetime_param(value, field_name):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError({field_name: "Use ISO 8601 datetime."})
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = datetime.combine(date_value, time.max)
    current_timezone = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, current_timezone),
        timezone.make_aware(end, current_timezone),
    )


transaction_filter_parameters = [
    OpenApiParameter("booking", int, OpenApiParameter.QUERY),
    OpenApiParameter("court", int, OpenApiParameter.QUERY),
    OpenApiParameter("payment_method", str, OpenApiParameter.QUERY),
    OpenApiParameter("date", str, OpenApiParameter.QUERY),
    OpenApiParameter("date_from", str, OpenApiParameter.QUERY),
    OpenApiParameter("date_to", str, OpenApiParameter.QUERY),
    OpenApiParameter("created_by", int, OpenApiParameter.QUERY),
]


@extend_schema_view(
    list=extend_schema(
        tags=["Transactions"],
        parameters=transaction_filter_parameters,
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
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        queryset = (
            self.get_access_context()
            .scoped_transactions_queryset()
            .select_related("booking", "club", "court", "created_by")
            .order_by("-created", "-id")
        )
        params = self.request.query_params

        if params.get("booking"):
            queryset = queryset.filter(booking_id=params["booking"])
        if params.get("court"):
            queryset = queryset.filter(court_id=params["court"])
        if params.get("payment_method"):
            queryset = queryset.filter(payment_method=params["payment_method"])
        if params.get("created_by"):
            queryset = queryset.filter(created_by_id=params["created_by"])

        if params.get("date"):
            start_of_day, end_of_day = day_bounds(
                parse_date_param(params["date"], "date")
            )
            queryset = queryset.filter(
                created__gte=start_of_day, created__lte=end_of_day
            )
        if params.get("date_from"):
            queryset = queryset.filter(
                created__gte=parse_datetime_param(params["date_from"], "date_from")
            )
        if params.get("date_to"):
            queryset = queryset.filter(
                created__lte=parse_datetime_param(params["date_to"], "date_to")
            )

        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return TransactionListSerializer
        if self.action == "create":
            return TransactionCreateSerializer
        return TransactionDetailSerializer
