from decimal import Decimal

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.bookings.models import Booking
from apps.bookings.services import create_booking, validate_booking_duration
from apps.courts.models import Court
from apps.transactions.services import get_booking_paid_amount


def format_money(value):
    return f"{Decimal(value or Decimal('0.00')):.2f}"


def get_paid_amount_for_booking(booking):
    annotated_value = getattr(booking, "paid_amount", None)
    if annotated_value is not None:
        return annotated_value
    return get_booking_paid_amount(booking)


class BookingPaymentSummaryMixin(serializers.Serializer):
    paid_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    is_fully_paid = serializers.SerializerMethodField()

    def get_paid_amount(self, obj):
        return format_money(get_paid_amount_for_booking(obj))

    def get_remaining_amount(self, obj):
        paid_amount = get_paid_amount_for_booking(obj)
        remaining_amount = obj.total_price - paid_amount
        return format_money(remaining_amount)

    def get_is_fully_paid(self, obj):
        return get_paid_amount_for_booking(obj) >= obj.total_price


class BookingListSerializer(BookingPaymentSummaryMixin, serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "club",
            "court",
            "customer_name",
            "customer_phone",
            "start_time",
            "end_time",
            "total_price",
            "paid_amount",
            "remaining_amount",
            "is_fully_paid",
            "status",
            "source",
            "created_by",
            "created",
        )
        read_only_fields = fields


class BookingDetailSerializer(BookingPaymentSummaryMixin, serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "club",
            "court",
            "customer_name",
            "customer_phone",
            "start_time",
            "end_time",
            "total_price",
            "paid_amount",
            "remaining_amount",
            "is_fully_paid",
            "status",
            "source",
            "notes",
            "cancellation_reason",
            "no_show_reason",
            "reschedule_reason",
            "completed_at",
            "cancelled_at",
            "no_show_at",
            "expired_at",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = (
            "id",
            "court",
            "customer_name",
            "customer_phone",
            "start_time",
            "end_time",
            "source",
            "notes",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "source": {"required": False},
            "notes": {"required": False},
        }

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        access = self.context["club_access"]
        court = attrs["court"]
        source = attrs.get("source", Booking.Source.MANUAL)

        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if not court.is_active:
            raise serializers.ValidationError(
                {"court": "Cannot create a booking on an inactive court."}
            )
        if not court.club.is_active:
            raise serializers.ValidationError(
                {"court": "Cannot create a booking for an inactive club."}
            )
        if not access.can_create_booking_for_court(court):
            raise PermissionDenied("You cannot create bookings for this court.")
        if source == Booking.Source.ADMIN_CORRECTION and not (
            user and user.is_platform_super_admin()
        ):
            raise serializers.ValidationError(
                {"source": "Only Platform Super Admin can use ADMIN_CORRECTION."}
            )

        validate_booking_duration(court, attrs["start_time"], attrs["end_time"])
        attrs["source"] = source
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        court = validated_data.pop("court")
        start_time = validated_data.pop("start_time")
        end_time = validated_data.pop("end_time")
        return create_booking(
            created_by=request.user,
            court=court,
            start_time=start_time,
            end_time=end_time,
            **validated_data,
        )

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data


class BookingCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class BookingNoShowSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class BookingRescheduleSerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(queryset=Court.objects.all())
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class BookingCompleteSerializer(serializers.Serializer):
    confirm_collect_remaining_cash = serializers.BooleanField(
        required=False,
        default=False,
    )


class BookingExpireSerializer(serializers.Serializer):
    pass


class BookingUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = (
            "customer_name",
            "customer_phone",
            "notes",
        )

    def validate(self, attrs):
        if self.instance.status in Booking.LOCKED_STATUSES:
            raise serializers.ValidationError(
                "This booking status cannot be edited in Sprint 3."
            )
        return attrs

    def update(self, instance, validated_data):
        before_data = {}
        after_data = {}
        for field, value in validated_data.items():
            old_value = getattr(instance, field)
            if old_value != value:
                before_data[field] = str(old_value)
                after_data[field] = str(value)

        with transaction.atomic():
            updated_booking = super().update(instance, validated_data)
            if before_data:
                request = self.context.get("request")
                actor = getattr(request, "user", None)
                record_audit_log(
                    club=updated_booking.club,
                    court=updated_booking.court,
                    actor=actor,
                    action=AuditLog.Action.BOOKING_UPDATED,
                    entity_type="Booking",
                    entity_id=updated_booking.id,
                    before_data=before_data,
                    after_data=after_data,
                )
            return updated_booking

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data
