from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from apps.bookings.identity import booking_customer_display_name
from apps.bookings.models import Booking
from apps.common.serializers import TimezoneAwareDateTimeField
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.services import create_booking_transaction


class BookingCustomerIdentityMixin(serializers.Serializer):
    """Booking identity sourced from the existing customer relations."""

    booking_customer_name = serializers.SerializerMethodField()
    booking_customer_phone = PhoneNumberField(
        source="booking.club_player.player_profile.phone_number",
        read_only=True,
    )

    @extend_schema_field(serializers.CharField())
    def get_booking_customer_name(self, obj) -> str:
        return booking_customer_display_name(obj.booking)


class TransactionResponseContextMixin(BookingCustomerIdentityMixin):
    """Transaction-only read context shared by list and detail responses."""

    is_settled = serializers.SerializerMethodField(
        help_text="True when the transaction is linked to a settlement."
    )

    def get_is_settled(self, obj) -> bool:
        return hasattr(obj, "settlement_line")


class TransactionListSerializer(
    TransactionResponseContextMixin, serializers.ModelSerializer
):
    created_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the transaction collector.",
    )
    cancelled_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who cancelled the transaction.",
    )
    booking_start_time = serializers.DateTimeField(
        source="booking.start_time",
        read_only=True,
    )
    booking_end_time = serializers.DateTimeField(
        source="booking.end_time",
        read_only=True,
    )
    court_name = serializers.CharField(source="court.name", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.get_display_name",
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
            "created_by_name",
            "is_settled",
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "created",
        )
        read_only_fields = fields


class TransactionDetailSerializer(
    TransactionResponseContextMixin, serializers.ModelSerializer
):
    created_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the transaction collector.",
    )
    cancelled_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who cancelled the transaction.",
    )
    booking_start_time = serializers.DateTimeField(
        source="booking.start_time",
        read_only=True,
    )
    booking_end_time = serializers.DateTimeField(
        source="booking.end_time",
        read_only=True,
    )
    court_name = serializers.CharField(source="court.name", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.get_display_name",
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
            "created_by_name",
            "client_request_id",
            "is_settled",
            "is_cancelled",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "occurred_at",
            "created",
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
    attempted_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who attempted the transaction.",
    )
    attempted_by_name = serializers.CharField(
        source="attempted_by.get_display_name",
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
            "attempted_by_name",
            "outcome",
            "resolution",
            "status",
            "failure_code",
            "resolved_transaction",
            "created",
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
        occurred_at_provided = validated_data.pop("_sloty_occurred_at_provided", False)
        access = self.context.get("access_context") or self.context.get("club_access")
        actor_profile = access.profile if access else None
        return create_booking_transaction(
            access=access,
            created_by=actor_profile,
            occurred_at_provided=occurred_at_provided,
            **validated_data,
        )

    def to_representation(self, instance):
        return TransactionDetailSerializer(instance, context=self.context).data
