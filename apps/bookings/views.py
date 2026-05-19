from datetime import datetime, time

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.viewsets import GenericViewSet

from apps.bookings.serializers import (
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingListSerializer,
    BookingUpdateSerializer,
)
from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageClubBookings


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_datetime(value):
    parsed = datetime.fromisoformat(value)
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


booking_filter_parameters = [
    OpenApiParameter("court", int, OpenApiParameter.QUERY),
    OpenApiParameter("status", str, OpenApiParameter.QUERY),
    OpenApiParameter("source", str, OpenApiParameter.QUERY),
    OpenApiParameter("date", str, OpenApiParameter.QUERY),
    OpenApiParameter("date_from", str, OpenApiParameter.QUERY),
    OpenApiParameter("date_to", str, OpenApiParameter.QUERY),
]


@extend_schema_view(
    list=extend_schema(
        tags=["Bookings"],
        parameters=booking_filter_parameters,
        responses=BookingListSerializer,
    ),
    create=extend_schema(
        tags=["Bookings"],
        request=BookingCreateSerializer,
        responses=BookingDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Bookings"], responses=BookingDetailSerializer),
    partial_update=extend_schema(
        tags=["Bookings"],
        request=BookingUpdateSerializer,
        responses=BookingDetailSerializer,
    ),
)
class BookingViewSet(
    ClubScopedAccessMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageClubBookings,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.bookings.models import Booking

            return Booking.objects.none()
        queryset = (
            self.get_access_context()
            .scoped_bookings_queryset()
            .select_related("club", "court", "created_by")
            .order_by("start_time", "id")
        )
        params = self.request.query_params

        if params.get("court"):
            queryset = queryset.filter(court_id=params["court"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("source"):
            queryset = queryset.filter(source=params["source"])

        date_value = parse_date(params.get("date"))
        if date_value:
            start_of_day, end_of_day = day_bounds(date_value)
            queryset = queryset.filter(
                start_time__lt=end_of_day,
                end_time__gt=start_of_day,
            )

        date_from = params.get("date_from")
        date_to = params.get("date_to")
        if date_from:
            queryset = queryset.filter(end_time__gt=parse_datetime(date_from))
        if date_to:
            queryset = queryset.filter(start_time__lt=parse_datetime(date_to))

        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return BookingListSerializer
        if self.action == "create":
            return BookingCreateSerializer
        if self.action in {"partial_update", "update"}:
            return BookingUpdateSerializer
        return BookingDetailSerializer
