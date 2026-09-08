from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import booking_audit_snapshot, record_audit_log
from apps.bookings.filters import compute_booking_hold_expires_at
from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.services import (
    FREE_SLOT_STATUS,
    MAX_SLOT_PERIOD_DAYS,
    RECURRING_RESERVED_SLOT_STATUS,
    UNAVAILABLE_SLOT_STATUS,
    create_booking,
    validate_booking_duration,
)
from apps.common.exceptions import SlotyAPIException
from apps.common.serializers import TimezoneAwareDateTimeField
from apps.courts.models import Court
from apps.transactions.models import Transaction
from apps.transactions.services import get_booking_paid_amount


def format_money(value):
    return f"{Decimal(value or Decimal('0.00')):.2f}"


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


class BookingHoldExpiresAtMixin(serializers.Serializer):
    hold_expires_at = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_hold_expires_at(self, obj):
        if obj.status != Booking.Status.HOLD:
            return None
        annotated_value = getattr(obj, "hold_expires_at", None)
        if annotated_value is not None:
            return annotated_value
        return compute_booking_hold_expires_at(obj)


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


class BookingListSerializer(
    BookingPaymentSummaryMixin,
    BookingHoldExpiresAtMixin,
    BookingRecurrenceReadMixin,
    serializers.ModelSerializer,
):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
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
            "notes",
            "created_by",
            "created",
        )
        read_only_fields = fields


class BookingDetailSerializer(
    BookingPaymentSummaryMixin,
    BookingHoldExpiresAtMixin,
    BookingRecurrenceReadMixin,
    serializers.ModelSerializer,
):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
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
            "cancellation_reason",
            "no_show_reason",
            "reschedule_reason",
            "completed_at",
            "cancelled_at",
            "no_show_at",
            "expired_at",
            "hold_expires_at",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
    is_recurring = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True,
    )
    requested_at = TimezoneAwareDateTimeField(required=False, write_only=True)

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
        request = self.context.get("request")
        user = getattr(request, "user", None)
        access = self.context["club_access"]
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
        if not access.can_create_booking_for_court(court):
            raise PermissionDenied("You cannot create bookings for this court.")
        if source == Booking.Source.ADMIN_CORRECTION and not (
            user and user.is_platform_super_admin()
        ):
            raise serializers.ValidationError(
                {"source": "Only Platform Super Admin can use ADMIN_CORRECTION."}
            )
        if source == Booking.Source.RECURRING:
            raise serializers.ValidationError(
                {"source": _("Use is_recurring=true to create recurring bookings.")}
            )
        if source == Booking.Source.ADMIN_CORRECTION and is_recurring:
            raise serializers.ValidationError(
                {"is_recurring": _("Admin correction cannot create a recurrence.")}
            )

        validate_booking_duration(court, attrs["start_time"], attrs["end_time"])
        attrs["source"] = Booking.Source.RECURRING if is_recurring else source
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        validated_data.pop("is_recurring", None)
        requested_at = validated_data.pop("requested_at", None)
        court = validated_data.pop("court")
        start_time = validated_data.pop("start_time")
        end_time = validated_data.pop("end_time")
        return create_booking(
            created_by=request.user,
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
    attempted_by_username = serializers.CharField(
        source="attempted_by.username",
        read_only=True,
        default="",
    )

    class Meta:
        model = BookingAttempt
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "attempted_by",
            "attempted_by_username",
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
            "modified",
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
        access = self.context["club_access"]
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
        if not access.can_view_court_availability(court):
            raise PermissionDenied("You cannot view availability for this court.")

        attrs["date_from"] = date_from
        attrs["date_to"] = date_to
        return attrs


class BookingSlotBookingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Booking.Status.choices)
    status_label = serializers.CharField()
    customer_name = serializers.CharField()
    customer_phone = serializers.CharField()
    total_booking_value = serializers.CharField()
    total_paid_amount = serializers.CharField()
    remaining_amount = serializers.CharField()
    source = serializers.ChoiceField(choices=Booking.Source.choices)
    is_recurring = serializers.BooleanField()
    recurrence_status = serializers.ChoiceField(
        choices=Booking.RecurrenceStatus.choices,
        allow_null=True,
    )


class BookingSlotRecurringContextSerializer(serializers.Serializer):
    anchor_booking_id = serializers.IntegerField()
    customer_name = serializers.CharField()
    customer_phone = serializers.CharField()
    recurrence_status = serializers.ChoiceField(
        choices=Booking.RecurrenceStatus.choices
    )


class BookingSlotSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
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
    is_available = serializers.BooleanField()
    booking = BookingSlotBookingSerializer(allow_null=True)
    recurring_anchor_booking_id = serializers.IntegerField(allow_null=True)
    recurring_context = BookingSlotRecurringContextSerializer(allow_null=True)
    can_start_recurring = serializers.BooleanField(allow_null=True)
    recurring_blocked_reason = serializers.CharField(allow_null=True)
    first_recurring_conflict_start = serializers.DateTimeField(allow_null=True)
    label = serializers.CharField()


class BookingSlotsResponseSerializer(serializers.Serializer):
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    slot_duration_minutes = serializers.IntegerField()
    message = serializers.CharField(required=False)
    slots = BookingSlotSerializer(many=True)


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
        changed = any(
            getattr(instance, field) != value for field, value in validated_data.items()
        )
        before_data = booking_audit_snapshot(instance) if changed else {}

        with transaction.atomic():
            updated_booking = super().update(instance, validated_data)
            if changed:
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
                    after_data=booking_audit_snapshot(updated_booking),
                )
            return updated_booking

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data
