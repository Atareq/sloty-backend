from decimal import Decimal

from rest_framework import serializers

from apps.bookings.models import Booking
from apps.transactions.models import Transaction
from apps.transactions.services import (
    create_booking_transaction,
    validate_booking_transaction_data,
)


class TransactionListSerializer(serializers.ModelSerializer):
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
    booking_customer_name = serializers.CharField(
        source="booking.customer_name",
        read_only=True,
    )
    booking_customer_phone = serializers.CharField(
        source="booking.customer_phone",
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
            "created_by",
            "created_by_username",
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "created",
        )
        read_only_fields = fields


class TransactionDetailSerializer(serializers.ModelSerializer):
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
    booking_customer_name = serializers.CharField(
        source="booking.customer_name",
        read_only=True,
    )
    booking_customer_phone = serializers.CharField(
        source="booking.customer_phone",
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
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
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


class TransactionCreateSerializer(serializers.ModelSerializer):
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all())
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "amount",
            "payment_method",
            "payment_reference",
            "notes",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "payment_reference": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        access = self.context["club_access"]
        booking = attrs["booking"]
        amount = attrs["amount"]
        payment_method = attrs["payment_method"]
        payment_reference = attrs.get("payment_reference", "")

        attrs["payment_reference"] = validate_booking_transaction_data(
            access=access,
            booking=booking,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
        )
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return create_booking_transaction(
            access=self.context["club_access"],
            created_by=request.user,
            **validated_data,
        )

    def to_representation(self, instance):
        return TransactionDetailSerializer(instance, context=self.context).data
