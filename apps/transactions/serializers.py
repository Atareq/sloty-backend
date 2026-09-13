from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)
from apps.bookings.models import Booking
from apps.common.serializers import TimezoneAwareDateTimeField
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.services import create_booking_transaction


class BookingCustomerIdentityMixin(serializers.Serializer):
    """Keep booking_customer_* keys; source ClubPlayer with snapshot fallback."""

    booking_customer_name = serializers.SerializerMethodField()
    booking_customer_phone = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_booking_customer_name(self, obj) -> str:
        return booking_customer_display_name(obj.booking)

    @extend_schema_field(serializers.CharField())
    def get_booking_customer_phone(self, obj) -> str:
        return booking_customer_display_phone(obj.booking)


class TransactionListSerializer(
    BookingCustomerIdentityMixin, serializers.ModelSerializer
):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    cancelled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    booking_start_time = serializers.DateTimeField(
        source="booking.start_time",
        read_only=True,
    )
    booking_end_time = serializers.DateTimeField(
        source="booking.end_time",
        read_only=True,
    )
    court_name = serializers.CharField(source="court.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username",
        read_only=True,
        default="",
    )

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "booking_customer_name",
            "booking_customer_phone",
            "booking_start_time",
            "booking_end_time",
            "club",
            "court",
            "court_name",
            "transaction_type",
            "amount",
            "client_request_id",
            "payment_method",
            "payment_reference",
            "occurred_at",
            "created_by",
            "created_by_username",
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "created",
        )
        read_only_fields = fields


class TransactionDetailSerializer(
    BookingCustomerIdentityMixin, serializers.ModelSerializer
):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    cancelled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    booking_start_time = serializers.DateTimeField(
        source="booking.start_time",
        read_only=True,
    )
    booking_end_time = serializers.DateTimeField(
        source="booking.end_time",
        read_only=True,
    )
    court_name = serializers.CharField(source="court.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username",
        read_only=True,
        default="",
    )

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "booking_customer_name",
            "booking_customer_phone",
            "booking_start_time",
            "booking_end_time",
            "club",
            "court",
            "court_name",
            "transaction_type",
            "amount",
            "payment_method",
            "payment_reference",
            "notes",
            "created_by",
            "created_by_username",
            "client_request_id",
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "occurred_at",
            "created",
            "modified",
        )
        read_only_fields = fields


class TransactionCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )


class TransactionAttemptStatusMixin(serializers.Serializer):
    status = serializers.SerializerMethodField()
    resolved_transaction = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_status(self, obj) -> str:
        if obj.resolution == TransactionAttempt.Resolution.DISMISSED:
            return "DISMISSED"
        if obj.outcome == TransactionAttempt.Outcome.SUCCESS:
            return "ACCEPTED"
        return "REJECTED"

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_resolved_transaction(self, obj):
        return obj.transaction_id


class TransactionAttemptListSerializer(
    BookingCustomerIdentityMixin,
    TransactionAttemptStatusMixin,
    serializers.ModelSerializer,
):
    court_name = serializers.CharField(source="court.name", read_only=True)
    attempted_by_username = serializers.CharField(
        source="attempted_by.username",
        read_only=True,
        default="",
    )

    class Meta:
        model = TransactionAttempt
        fields = (
            "id",
            "booking",
            "booking_customer_name",
            "booking_customer_phone",
            "club",
            "court",
            "court_name",
            "amount",
            "payment_method",
            "payment_reference",
            "notes",
            "client_request_id",
            "occurred_at",
            "attempted_by",
            "attempted_by_username",
            "outcome",
            "resolution",
            "status",
            "failure_code",
            "resolved_transaction",
            "created",
            "modified",
        )
        read_only_fields = fields


class TransactionAttemptDetailSerializer(TransactionAttemptListSerializer):
    class Meta(TransactionAttemptListSerializer.Meta):
        fields = TransactionAttemptListSerializer.Meta.fields + ("failure_details",)


class TransactionAttemptDismissSerializer(serializers.Serializer):
    pass


class TransactionCreateSerializer(serializers.ModelSerializer):
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all())
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    occurred_at = TimezoneAwareDateTimeField(required=False)

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "amount",
            "client_request_id",
            "payment_method",
            "payment_reference",
            "notes",
            "occurred_at",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "payment_reference": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        occurred_at_provided = "occurred_at" in self.initial_data
        attrs["_sloty_occurred_at_provided"] = occurred_at_provided
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        occurred_at_provided = validated_data.pop("_sloty_occurred_at_provided", False)
        access = self.context.get("access_context") or self.context.get("club_access")
        return create_booking_transaction(
            access=access,
            created_by=request.user,
            occurred_at_provided=occurred_at_provided,
            **validated_data,
        )

    def to_representation(self, instance):
        return TransactionDetailSerializer(instance, context=self.context).data
