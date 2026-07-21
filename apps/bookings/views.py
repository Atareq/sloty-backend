from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
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
    BookingCancelSerializer,
    BookingCompleteSerializer,
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingExpireSerializer,
    BookingListSerializer,
    BookingNoShowSerializer,
    BookingRescheduleSerializer,
    BookingSlotQuerySerializer,
    BookingSlotsResponseSerializer,
    BookingUpdateSerializer,
)
from apps.bookings.services import (
    cancel_booking,
    complete_booking,
    expire_booking,
    generate_booking_slots,
    no_show_booking,
    reschedule_booking,
)
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
                    Sum(
                        "transactions__amount",
                        filter=Q(transactions__is_cancelled=False),
                    ),
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
        if self.action == "slots":
            return BookingSlotQuerySerializer
        if self.action in {"partial_update", "update"}:
            return BookingUpdateSerializer
        if self.action == "cancel":
            return BookingCancelSerializer
        if self.action == "complete":
            return BookingCompleteSerializer
        if self.action == "no_show":
            return BookingNoShowSerializer
        if self.action == "reschedule":
            return BookingRescheduleSerializer
        if self.action == "expire":
            return BookingExpireSerializer
        return BookingDetailSerializer

    def get_lifecycle_booking(self, access):
        return get_object_or_404(
            Booking.objects.select_related("club", "court"),
            pk=self.kwargs[self.lookup_url_kwarg or self.lookup_field],
            club=access.club,
        )

    def lifecycle_response(self, booking):
        serializer = BookingDetailSerializer(
            booking, context=self.get_serializer_context()
        )
        return Response(serializer.data)

    def validate_action_payload(self):
        serializer = self.get_serializer(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_lifecycle_context(self):
        access = self.get_access_context()
        return access, self.get_lifecycle_booking(access)

    @extend_schema(
        tags=["Bookings"],
        parameters=[BookingSlotQuerySerializer],
        responses=BookingSlotsResponseSerializer,
    )
    @action(detail=False, methods=["get"])
    def slots(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=self.request.GET)
        serializer.is_valid(raise_exception=True)
        access = self.get_access_context()
        data = generate_booking_slots(access=access, **serializer.validated_data)
        return Response(data)

    @extend_schema(
        tags=["Bookings"],
        request=BookingCancelSerializer,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        data = self.validate_action_payload()
        access, booking = self.get_lifecycle_context()
        booking = cancel_booking(
            access=access,
            booking=booking,
            actor=request.user,
            reason=data.get("reason", ""),
        )
        return self.lifecycle_response(booking)

    @extend_schema(
        tags=["Bookings"],
        request=BookingCompleteSerializer,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def complete(self, request, *args, **kwargs):
        data = self.validate_action_payload()
        access, booking = self.get_lifecycle_context()
        booking = complete_booking(
            access=access,
            booking=booking,
            actor=request.user,
            confirm_collect_remaining_cash=data["confirm_collect_remaining_cash"],
        )
        return self.lifecycle_response(booking)

    @extend_schema(
        tags=["Bookings"],
        request=BookingNoShowSerializer,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, *args, **kwargs):
        data = self.validate_action_payload()
        access, booking = self.get_lifecycle_context()
        booking = no_show_booking(
            access=access,
            booking=booking,
            actor=request.user,
            reason=data.get("reason", ""),
        )
        return self.lifecycle_response(booking)

    @extend_schema(
        tags=["Bookings"],
        request=BookingRescheduleSerializer,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def reschedule(self, request, *args, **kwargs):
        data = self.validate_action_payload()
        access, booking = self.get_lifecycle_context()
        booking = reschedule_booking(
            access=access,
            booking=booking,
            actor=request.user,
            court=data["court"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            reason=data.get("reason", ""),
        )
        return self.lifecycle_response(booking)

    @extend_schema(
        tags=["Bookings"],
        request=BookingExpireSerializer,
        responses=BookingDetailSerializer,
    )
    @action(detail=True, methods=["post"])
    def expire(self, request, *args, **kwargs):
        self.validate_action_payload()
        access, booking = self.get_lifecycle_context()
        booking = expire_booking(access=access, booking=booking, actor=request.user)
        return self.lifecycle_response(booking)
