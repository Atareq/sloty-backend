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

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "club",
            "court",
            "amount",
            "payment_method",
            "payment_reference",
            "created_by",
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

    class Meta:
        model = Transaction
        fields = (
            "id",
            "booking",
            "club",
            "court",
            "amount",
            "payment_method",
            "payment_reference",
            "notes",
            "created_by",
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
