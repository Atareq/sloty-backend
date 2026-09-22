from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.bookings.identity import booking_customer_display_name
from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.services import (
    FREE_SLOT_STATUS,
    MAX_SLOT_PERIOD_DAYS,
    RECURRING_RESERVED_SLOT_STATUS,
    UNAVAILABLE_SLOT_STATUS,
    create_booking,
    update_booking,
    validate_booking_duration,
)
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.scopes import ResourceScope
from apps.common.exceptions import SlotyAPIException
from apps.common.serializers import TimezoneAwareDateTimeField
from apps.courts.models import Court
from apps.transactions.models import Transaction
from apps.transactions.services import get_booking_paid_amount


def format_money(value):
    return f"{Decimal(value or Decimal('0.00')):.2f}"


def _serializer_access(serializer):
    request = serializer.context.get("request")
    return (
        serializer.context.get("access_context")
        or serializer.context.get("club_access")
        or getattr(request, "access_context", None)
    )


def _court_is_in_authorized_scope(access, court):
    if access is None or court is None:
        return False
    return (
        scoped_queryset(access, Court, scope=ResourceScope.COURT)
        .filter(pk=court.pk)
        .exists()
    )


def get_paid_amount_for_booking(booking):
    annotated_value = getattr(booking, "paid_amount", None)
    if annotated_value is not None:
        return annotated_value
    paid_amount = get_booking_paid_amount(booking)
    booking.paid_amount = paid_amount
    return paid_amount


class BookingPaymentSummaryMixin(serializers.Serializer):
    paid_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    is_fully_paid = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_paid_amount(self, obj):
        return format_money(get_paid_amount_for_booking(obj))

    @extend_schema_field(serializers.CharField())
    def get_remaining_amount(self, obj):
        paid_amount = get_paid_amount_for_booking(obj)
        remaining_amount = obj.total_price - paid_amount
        return format_money(remaining_amount)

    @extend_schema_field(serializers.BooleanField())
    def get_is_fully_paid(self, obj):
        return get_paid_amount_for_booking(obj) >= obj.total_price


class BookingDatetimeReadMixin(serializers.Serializer):
    start_time = serializers.DateTimeField(
        read_only=True,
        help_text="Timezone-aware ISO-8601 booking start datetime.",
    )
    end_time = serializers.DateTimeField(
        read_only=True,
        help_text="Timezone-aware ISO-8601 booking end datetime.",
    )


class BookingTerminationReasonReadMixin(serializers.Serializer):
    termination_reason = serializers.CharField(
        read_only=True,
        help_text=(
            "Reason supplied for the latest cancellation, no-show, or "
            "reschedule action. Earlier events remain in the audit log."
        ),
    )


class BookingHoldExpiresAtMixin(serializers.Serializer):
    hold_expires_at = serializers.SerializerMethodField()

    @extend_schema_field(
        serializers.DateTimeField(
            allow_null=True,
            help_text=(
                "Timezone-aware ISO-8601 deadline for this HOLD; null when the "
                "booking is not on hold."
            ),
        )
    )
    def get_hold_expires_at(self, obj):
        if obj.status != Booking.Status.HOLD:
            return None
        return obj.hold_expires_at


class BookingRecurrenceReadMixin:
    is_recurring = serializers.SerializerMethodField()
    next_recurring_booking_id = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_is_recurring(self, obj):
        return obj.source == Booking.Source.RECURRING

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_next_recurring_booking_id(self, obj):
        next_booking = getattr(obj, "next_recurring_booking", None)
        return next_booking.id if next_booking else None


class BookingIdentityReadMixin(serializers.Serializer):
    """
    Operational booking responses keep customer_name / customer_phone keys
    and read them from Booking.club_player → PlayerProfile.
    """

    customer_name = serializers.SerializerMethodField()
    customer_phone = PhoneNumberField(
        source="club_player.player_profile.phone_number",
        read_only=True,
    )

    @extend_schema_field(serializers.CharField())
    def get_customer_name(self, obj) -> str:
        return booking_customer_display_name(obj)


class BookingLastStatusActorMixin(serializers.Serializer):
    last_status_changed_by = serializers.SerializerMethodField()
    last_status_changed_by_name = serializers.SerializerMethodField()

    @extend_schema_field(
        serializers.IntegerField(
            allow_null=True,
            help_text="Profile ID of the actor who last changed booking status.",
        )
    )
    def get_last_status_changed_by(self, obj):
        if obj.last_status_changed_by_type == Booking.LastStatusActorType.PROFILE:
            return obj.last_status_changed_by_id
        return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_last_status_changed_by_name(self, obj):
        if obj.last_status_changed_by_type == Booking.LastStatusActorType.SYSTEM:
            return "System (Automatic)"
        if (
            obj.last_status_changed_by_type == Booking.LastStatusActorType.PROFILE
            and obj.last_status_changed_by is not None
        ):
            actor_user = getattr(obj.last_status_changed_by, "user", None)
            if actor_user:
                return actor_user.get_display_name()
            return str(obj.last_status_changed_by)
        return None


class BookingListSerializer(
    BookingIdentityReadMixin,
    BookingDatetimeReadMixin,
    BookingTerminationReasonReadMixin,
    BookingPaymentSummaryMixin,
    BookingHoldExpiresAtMixin,
    BookingRecurrenceReadMixin,
    BookingLastStatusActorMixin,
    serializers.ModelSerializer,
):
    created_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the booking creator.",
    )
    court_name = serializers.CharField(source="court.name", read_only=True)
    is_recurring = serializers.SerializerMethodField()
    next_recurring_booking_id = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            "id",
            "club",
            "court",
            "court_name",
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
            "is_recurring",
            "recurrence_status",
            "previous_recurring_booking_id",
            "next_recurring_booking_id",
            "client_request_id",
            "hold_expires_at",
            "termination_reason",
            "notes",
            "last_status_changed_by",
            "last_status_changed_by_name",
            "created_by",
            "created",
        )
        read_only_fields = fields


class BookingDetailSerializer(
    BookingIdentityReadMixin,
    BookingDatetimeReadMixin,
    BookingTerminationReasonReadMixin,
    BookingPaymentSummaryMixin,
    BookingHoldExpiresAtMixin,
    BookingRecurrenceReadMixin,
    BookingLastStatusActorMixin,
    serializers.ModelSerializer,
):
    created_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the booking creator.",
    )
    is_recurring = serializers.SerializerMethodField()
    next_recurring_booking_id = serializers.SerializerMethodField()

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
            "is_recurring",
            "recurrence_status",
            "previous_recurring_booking_id",
            "next_recurring_booking_id",
            "client_request_id",
            "notes",
            "termination_reason",
            "completed_at",
            "cancelled_at",
            "no_show_at",
            "expired_at",
            "hold_expires_at",
            "last_status_changed_by",
            "last_status_changed_by_name",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
    start_time = TimezoneAwareDateTimeField(
        help_text="Timezone-aware ISO-8601 booking start datetime."
    )
    end_time = TimezoneAwareDateTimeField(
        help_text="Timezone-aware ISO-8601 booking end datetime."
    )
    customer_name = serializers.CharField(
        max_length=255,
        write_only=True,
        help_text=(
            "Customer display name for this booking. Exact match against "
            "ClubPlayer.display_name within the club; creates a new ClubPlayer "
            "if no exact name match exists."
        ),
    )
    customer_phone = PhoneNumberField(
        write_only=True,
        help_text=(
            "Customer phone number. Global identity key resolving to PlayerProfile."
        ),
    )
    is_recurring = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True,
    )

    requested_at = TimezoneAwareDateTimeField(required=False, write_only=True)
    club_player_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text=(
            "Optional. Explicit ClubPlayer version to attach. Omit for "
            "normal creation (backend uses the current version). Supply a "
            "historical version id to book under an older club identity."
        ),
    )

    class Meta:
        model = Booking
        fields = (
            "id",
            "court",
            "customer_name",
            "customer_phone",
            "club_player_id",
            "start_time",
            "end_time",
            "source",
            "is_recurring",
            "client_request_id",
            "requested_at",
            "notes",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "source": {"required": False},
            "notes": {"required": False},
        }

    def validate(self, attrs):
        access = _serializer_access(self)
        court = attrs["court"]
        source = attrs.get("source", Booking.Source.MANUAL)
        is_recurring = attrs.get("is_recurring", False)

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
        if not _court_is_in_authorized_scope(access, court):
            raise PermissionDenied("You cannot create bookings for this court.")
        if source == Booking.Source.RECURRING:
            raise serializers.ValidationError(
                {"source": _("Use is_recurring=true to create recurring bookings.")}
            )
        validate_booking_duration(court, attrs["start_time"], attrs["end_time"])
        attrs["source"] = Booking.Source.RECURRING if is_recurring else source
        return attrs

    def create(self, validated_data):
        access = _serializer_access(self)
        validated_data.pop("is_recurring", None)
        requested_at = validated_data.pop("requested_at", None)
        court = validated_data.pop("court")
        start_time = validated_data.pop("start_time")
        end_time = validated_data.pop("end_time")
        return create_booking(
            created_by=access.profile if access else None,
            court=court,
            start_time=start_time,
            end_time=end_time,
            requested_at=requested_at,
            **validated_data,
        )

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data


class BookingAttemptStatusMixin(serializers.Serializer):
    status = serializers.SerializerMethodField()
    resolved_booking = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_status(self, obj) -> str:
        if obj.resolution == BookingAttempt.Resolution.DISMISSED:
            return "DISMISSED"
        if obj.outcome == BookingAttempt.Outcome.SUCCESS:
            return "ACCEPTED"
        return "REJECTED"

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_resolved_booking(self, obj):
        return obj.booking_id


class BookingAttemptListSerializer(
    BookingAttemptStatusMixin,
    serializers.ModelSerializer,
):
    court_name = serializers.CharField(source="court.name", read_only=True)
    attempted_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who attempted the booking request.",
    )
    attempted_by_name = serializers.CharField(
        source="attempted_by.get_display_name",
        read_only=True,
        default="",
    )
    customer_phone = PhoneNumberField(read_only=True)

    class Meta:
        model = BookingAttempt
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "attempted_by",
            "attempted_by_name",
            "client_request_id",
            "customer_name",
            "customer_phone",
            "notes",
            "requested_start",
            "requested_end",
            "requested_at",
            "requested_source",
            "requested_recurring",
            "outcome",
            "resolution",
            "status",
            "failure_code",
            "resolved_booking",
            "created",
        )
        read_only_fields = fields


class BookingAttemptDetailSerializer(BookingAttemptListSerializer):
    class Meta(BookingAttemptListSerializer.Meta):
        fields = BookingAttemptListSerializer.Meta.fields + ("failure_details",)


class BookingAttemptDismissSerializer(serializers.Serializer):
    pass


class BookingCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        help_text="Cancellation reason; returned as termination_reason.",
    )
    refund_payment_method = serializers.ChoiceField(
        choices=Transaction.PaymentMethod.choices,
        required=False,
        allow_null=True,
    )
    refund_reference = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )
    refund_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )


class BookingRecurrenceNextSerializer(serializers.Serializer):
    can_continue = serializers.BooleanField()
    next_start_time = serializers.DateTimeField()
    next_end_time = serializers.DateTimeField()
    next_total_price = serializers.CharField()
    next_required_deposit = serializers.CharField()
    requires_digital_payment_reference = serializers.BooleanField(
        help_text=(
            "If true, this court requires payment_reference when the next "
            "deposit uses DIGITAL_WALLET or BANK_TRANSFER. CASH does not "
            "require a reference."
        )
    )
    requires_payment_reference = serializers.BooleanField(
        help_text=(
            "Deprecated alias of requires_digital_payment_reference. "
            "Does not mean CASH requires a reference."
        )
    )


class BookingCancellationPreviewResponseSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()
    previewed_at = serializers.DateTimeField()
    booking_start = serializers.DateTimeField()
    paid_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    minimum_deposit = serializers.DecimalField(max_digits=10, decimal_places=2)
    refund_notice_days = serializers.IntegerField(allow_null=True)
    refund_deadline = serializers.DateTimeField()
    full_refund = serializers.BooleanField()
    refund_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    retained_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    can_cancel = serializers.BooleanField()


class BookingNoShowSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        help_text="No-show reason; returned as termination_reason.",
    )


class BookingRescheduleSerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(queryset=Court.objects.all())
    start_time = TimezoneAwareDateTimeField(
        help_text="Timezone-aware ISO-8601 booking start datetime."
    )
    end_time = TimezoneAwareDateTimeField(
        help_text="Timezone-aware ISO-8601 booking end datetime."
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        help_text="Reschedule reason; returned as termination_reason.",
    )

    def validate(self, attrs):
        access = _serializer_access(self)
        court = attrs["court"]
        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if not _court_is_in_authorized_scope(access, court):
            raise PermissionDenied("You cannot reschedule bookings to this court.")
        return attrs


class BookingCompleteSerializer(serializers.Serializer):
    confirm_collect_remaining_cash = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated no-op kept for compatibility. The backend never "
            "auto-creates a remaining cash transaction. Completion is rejected "
            "when remaining_amount is greater than zero."
        ),
    )
    continue_recurring = serializers.BooleanField(
        required=False,
        allow_null=True,
    )
    next_deposit_payment_method = serializers.ChoiceField(
        choices=Transaction.PaymentMethod.choices,
        required=False,
        allow_null=True,
    )
    next_deposit_payment_reference = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )
    next_deposit_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )


class BookingExpireSerializer(serializers.Serializer):
    pass


class BookingEndRecurrenceSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class BookingSlotQuerySerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.prefetch_related("working_hours__pricing_periods")
    )

    date = serializers.DateField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        access = _serializer_access(self)
        court = attrs["court"]
        date = attrs.get("date")
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")

        if date is not None:
            if date_from is not None or date_to is not None:
                raise serializers.ValidationError(
                    {"date": _("Use either date or date_from/date_to.")}
                )
            date_from = date
            date_to = date
            attrs.pop("date", None)
        elif date_from is None or date_to is None:
            raise serializers.ValidationError(
                {"date": _("Provide date or both date_from and date_to.")}
            )

        if date_from > date_to:
            raise serializers.ValidationError({"date_to": _("Invalid slot period.")})
        if (date_to - date_from).days + 1 > MAX_SLOT_PERIOD_DAYS:
            raise SlotyAPIException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="SLOT_PERIOD_TOO_LARGE",
                message=_("The requested slot period is too large."),
            )
        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": _("Court must belong to the selected club.")}
            )
        if not _court_is_in_authorized_scope(access, court):
            raise PermissionDenied("You cannot view availability for this court.")

        attrs["date_from"] = date_from
        attrs["date_to"] = date_to
        return attrs


class BookingSlotBookingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Booking.Status.choices)
    status_label = serializers.CharField()
    customer_name = serializers.CharField()
    customer_phone = PhoneNumberField(allow_blank=True)
    total_booking_value = serializers.CharField()
    total_paid_amount = serializers.CharField()
    remaining_amount = serializers.CharField()
    hold_expires_at = serializers.DateTimeField(
        allow_null=True,
        help_text=(
            "Timezone-aware ISO-8601 deadline for a blocking HOLD; null for "
            "other booking statuses."
        ),
    )
    source = serializers.ChoiceField(choices=Booking.Source.choices)
    is_recurring = serializers.BooleanField()
    recurrence_status = serializers.ChoiceField(
        choices=Booking.RecurrenceStatus.choices,
        allow_null=True,
    )


class BookingSlotRecurringContextSerializer(serializers.Serializer):
    anchor_booking_id = serializers.IntegerField()
    customer_name = serializers.CharField()
    customer_phone = PhoneNumberField(allow_blank=True)
    recurrence_status = serializers.ChoiceField(
        choices=Booking.RecurrenceStatus.choices
    )


class BookingSlotSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.DateTimeField(
        help_text="Timezone-aware ISO-8601 slot start datetime; use as returned."
    )
    end_time = serializers.DateTimeField(
        help_text="Timezone-aware ISO-8601 slot end datetime; use as returned."
    )
    slot_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        allow_null=True,
    )
    slot_status = serializers.ChoiceField(
        choices=[
            (FREE_SLOT_STATUS, FREE_SLOT_STATUS),
            (UNAVAILABLE_SLOT_STATUS, UNAVAILABLE_SLOT_STATUS),
            (RECURRING_RESERVED_SLOT_STATUS, RECURRING_RESERVED_SLOT_STATUS),
        ]
        + list(Booking.Status.choices)
    )
    is_available = serializers.BooleanField(
        help_text=(
            "Backend-calculated availability; clients should display it as returned."
        )
    )
    available = serializers.BooleanField(
        help_text="Backend-calculated availability for frontend scheduling consumers."
    )
    booking = BookingSlotBookingSerializer(allow_null=True)
    recurring_anchor_booking_id = serializers.IntegerField(allow_null=True)
    recurring_context = BookingSlotRecurringContextSerializer(allow_null=True)
    can_start_recurring = serializers.BooleanField(allow_null=True)
    recurring_blocked_reason = serializers.CharField(allow_null=True)
    first_recurring_conflict_start = serializers.DateTimeField(allow_null=True)
    label = serializers.CharField()
    slot_type = serializers.ChoiceField(choices=("GENERATED", "BOOKING_ONLY"))


class BookingSlotsResponseSerializer(serializers.Serializer):
    slots = BookingSlotSerializer(many=True)


class BookingUpdateSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        max_length=255,
        required=False,
        write_only=True,
    )
    customer_phone = PhoneNumberField(
        required=False,
        write_only=True,
    )

    class Meta:
        model = Booking
        fields = ("notes", "customer_name", "customer_phone")
        extra_kwargs = {
            "notes": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        if self.instance.status in Booking.LOCKED_STATUSES:
            raise serializers.ValidationError(
                "This booking status cannot be edited in Sprint 3."
            )
        return attrs

    def update(self, instance, validated_data):
        access = _serializer_access(self)
        actor = access.profile if access else None
        return update_booking(
            booking=instance,
            updated_by=actor,
            **validated_data,
        )

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data
