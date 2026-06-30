from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.bookings.filters import BookingFilter
from apps.bookings.models import Booking
from apps.bookings.serializers import (
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingListSerializer,
    BookingUpdateSerializer,
)
from apps.bookings.services import transition_booking_status
from apps.clubs.mixins import ClubScopedAccessMixin
from apps.clubs.permissions import CanManageClubBookings


@extend_schema_view(
    list=extend_schema(
        tags=["Bookings"],
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
    filter_backends = (DjangoFilterBackend,)
    filterset_class = BookingFilter
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.bookings.models import Booking

            return Booking.objects.none()
        return (
            self.get_access_context()
            .scoped_bookings_queryset()
            .select_related("club", "court", "created_by")
            .annotate(
                paid_amount=Coalesce(
                    Sum("transactions__amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
            .order_by("start_time", "id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return BookingListSerializer
        if self.action == "create":
            return BookingCreateSerializer
        if self.action in {"partial_update", "update"}:
            return BookingUpdateSerializer
        return BookingDetailSerializer

    def run_status_transition(self, target_status):
        access = self.get_access_context()
        booking = get_object_or_404(
            Booking.objects.select_related("club", "court"),
            pk=self.kwargs[self.lookup_url_kwarg or self.lookup_field],
            club=access.club,
        )
        booking = transition_booking_status(
            access=access,
            booking=booking,
            target_status=target_status,
            actor=self.request.user,
        )
        serializer = BookingDetailSerializer(
            booking, context=self.get_serializer_context()
        )
        return Response(serializer.data)

    @extend_schema(
        tags=["Bookings"],
        request=None,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        return self.run_status_transition(Booking.Status.CANCELLED)

    @extend_schema(
        tags=["Bookings"],
        request=None,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def complete(self, request, *args, **kwargs):
        return self.run_status_transition(Booking.Status.COMPLETED)

    @extend_schema(
        tags=["Bookings"],
        request=None,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, *args, **kwargs):
        return self.run_status_transition(Booking.Status.NO_SHOW)

    @extend_schema(
        tags=["Bookings"],
        request=None,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def expire(self, request, *args, **kwargs):
        return self.run_status_transition(Booking.Status.EXPIRED)
