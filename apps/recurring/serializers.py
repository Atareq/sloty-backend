from rest_framework import serializers

from apps.courts.models import Court
from apps.recurring.models import RecurringAgreement
from apps.recurring.services import (
    build_cancellation_preview,
    cancel_recurring_agreement,
    create_recurring_agreement,
    preview_recurring_availability,
    refund_recurring_deposit,
)
from apps.transactions.models import Transaction


class RecurringAgreementListSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    deposit_collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    cancelled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    refunded_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = RecurringAgreement
        fields = (
            "id",
            "club",
            "court",
            "customer_name",
            "customer_phone",
            "weekday",
            "start_time",
            "end_time",
            "start_date",
            "status",
            "deposit_amount",
            "deposit_status",
            "deposit_collected_at",
            "deposit_collected_by",
            "cancellation_requested_at",
            "cancellation_effective_date",
            "cancelled_by",
            "refund_due_at",
            "refunded_at",
            "refunded_by",
            "action_required_code",
            "failed_occurrence_start",
            "action_required_at",
            "created_by",
            "created",
        )
        read_only_fields = fields


class RecurringAgreementDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    deposit_collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    cancelled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    refunded_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = RecurringAgreement
        fields = (
            "id",
            "club",
            "court",
            "customer_name",
            "customer_phone",
            "weekday",
            "start_time",
            "end_time",
            "start_date",
            "status",
            "deposit_amount",
            "deposit_status",
            "deposit_collected_at",
            "deposit_collected_by",
            "cancellation_requested_at",
            "cancellation_effective_date",
            "cancelled_by",
            "cancellation_reason",
            "refund_due_at",
            "refunded_at",
            "refunded_by",
            "action_required_code",
            "failed_occurrence_start",
            "action_required_at",
            "notes",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = fields


class RecurringAgreementCreateSerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(queryset=Court.objects.all())
    customer_name = serializers.CharField(max_length=255)
    customer_phone = serializers.CharField()
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    start_date = serializers.DateField()
    payment_method = serializers.ChoiceField(choices=Transaction.PaymentMethod.choices)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        access = self.context["club_access"]
        court = attrs["court"]
        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if attrs["start_time"] >= attrs["end_time"]:
            raise serializers.ValidationError(
                {"end_time": "end_time must be after start_time."}
            )
        return attrs

    def create(self, validated_data):
        access = self.context["club_access"]
        return create_recurring_agreement(
            access=access,
            created_by=self.context["request"].user,
            **validated_data,
        )

    def to_representation(self, instance):
        return RecurringAgreementDetailSerializer(instance, context=self.context).data


class RecurringAvailabilityQuerySerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(queryset=Court.objects.all())
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    start_date = serializers.DateField()

    def validate(self, attrs):
        access = self.context["club_access"]
        court = attrs["court"]
        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if attrs["start_time"] >= attrs["end_time"]:
            raise serializers.ValidationError(
                {"end_time": "end_time must be after start_time."}
            )
        return attrs

    def build_preview(self):
        access = self.context["club_access"]
        return preview_recurring_availability(
            access=access,
            **self.validated_data,
        )


class RecurringCancellationPreviewSerializer(serializers.Serializer):
    effective_date = serializers.DateField(required=False, allow_null=True)

    def build_preview(self, agreement):
        access = self.context["club_access"]
        return build_cancellation_preview(
            access=access,
            agreement=agreement,
            effective_date=self.validated_data.get("effective_date"),
        )


class RecurringCancelSerializer(serializers.Serializer):
    effective_date = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, agreement):
        access = self.context["club_access"]
        return cancel_recurring_agreement(
            access=access,
            agreement=agreement,
            effective_date=self.validated_data.get("effective_date"),
            reason=self.validated_data.get("reason", ""),
            actor=self.context["request"].user,
        )


class RecurringRefundDepositSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=Transaction.PaymentMethod.choices)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, agreement):
        access = self.context["club_access"]
        agreement, _refund_tx = refund_recurring_deposit(
            access=access,
            agreement=agreement,
            payment_method=self.validated_data["payment_method"],
            reference=self.validated_data.get("reference", ""),
            notes=self.validated_data.get("notes", ""),
            actor=self.context["request"].user,
        )
        return agreement
