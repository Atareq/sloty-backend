from decimal import Decimal
from typing import Optional

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from apps.bookings.identity import booking_customer_display_name
from apps.courts.models import Court
from apps.profiles.models import Profile
from apps.settlements.authorization import (
    can_approve_collector,
    can_manage_settlements,
    can_view_collector_in_summary,
    get_active_profile_roles_by_id,
    validate_preview_authority,
    validate_settlement_authority,
    validate_unsettled_summary_authority,
)
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import create as create_settlement
from apps.settlements.services import preview as preview_settlement
from apps.transactions.models import Transaction

ZERO = Decimal("0.00")


def profile_display_name(profile: Optional[Profile]) -> str:
    if profile is None:
        return ""
    return profile.get_display_name()


class SettlementListSerializer(serializers.ModelSerializer):
    settled_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who performed settlement.",
    )
    settled_by_name = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()
    transaction_count = serializers.IntegerField(read_only=True)
    period_start = serializers.DateTimeField(read_only=True)
    period_end = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "total_amount",
            "transaction_count",
            "period_start",
            "period_end",
            "settled_by",
            "settled_by_name",
            "created",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_settled_by_name(self, obj):
        return profile_display_name(obj.settled_by)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_court_name(self, obj):
        if obj.court_id is None:
            return None
        return obj.court.name


class SettlementLineSerializer(serializers.ModelSerializer):
    transaction = serializers.PrimaryKeyRelatedField(read_only=True)
    booking = serializers.IntegerField(source="transaction.booking_id", read_only=True)
    booking_customer_name = serializers.SerializerMethodField()
    booking_customer_phone = PhoneNumberField(
        source="transaction.booking.club_player.player_profile.phone_number",
        read_only=True,
    )
    booking_start_time = serializers.DateTimeField(
        source="transaction.booking.start_time",
        read_only=True,
    )
    booking_end_time = serializers.DateTimeField(
        source="transaction.booking.end_time",
        read_only=True,
    )
    court = serializers.IntegerField(source="transaction.court_id", read_only=True)
    court_name = serializers.CharField(source="transaction.court.name", read_only=True)
    amount = serializers.DecimalField(
        source="transaction.amount", max_digits=10, decimal_places=2, read_only=True
    )
    payment_method = serializers.CharField(
        source="transaction.payment_method",
        read_only=True,
    )
    transaction_type = serializers.CharField(
        source="transaction.transaction_type",
        read_only=True,
    )
    payment_reference = serializers.CharField(
        source="transaction.payment_reference",
        read_only=True,
    )
    transaction_created = serializers.DateTimeField(
        source="transaction.created",
        read_only=True,
    )
    created = serializers.DateTimeField(source="transaction.created", read_only=True)
    kind = serializers.SerializerMethodField()

    class Meta:
        model = SettlementTransaction
        fields = (
            "id",
            "kind",
            "transaction",
            "transaction_type",
            "booking",
            "booking_customer_name",
            "booking_customer_phone",
            "booking_start_time",
            "booking_end_time",
            "court",
            "court_name",
            "amount",
            "payment_method",
            "payment_reference",
            "transaction_created",
            "created",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_kind(self, obj):
        return obj.transaction.transaction_type

    @extend_schema_field(serializers.CharField())
    def get_booking_customer_name(self, obj) -> str:
        return booking_customer_display_name(obj.transaction.booking)


class SettlementDetailSerializer(serializers.ModelSerializer):
    settled_by = serializers.PrimaryKeyRelatedField(
        read_only=True,
        help_text="Profile ID of the actor who performed settlement.",
    )
    settled_by_name = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()
    transaction_count = serializers.IntegerField(read_only=True)
    period_start = serializers.DateTimeField(read_only=True)
    period_end = serializers.DateTimeField(read_only=True)
    lines = SettlementLineSerializer(many=True, read_only=True)
    transactions = SettlementLineSerializer(source="lines", many=True, read_only=True)

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "total_amount",
            "transaction_count",
            "period_start",
            "period_end",
            "notes",
            "settled_by",
            "settled_by_name",
            "created",
            "lines",
            "transactions",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_settled_by_name(self, obj):
        return profile_display_name(obj.settled_by)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_court_name(self, obj):
        if obj.court_id is None:
            return None
        return obj.court.name


class SettlementCreateSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=Profile.objects.all(),
        required=False,
        allow_null=True,
        help_text="Collector Profile ID stored in Transaction.created_by.",
    )
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Settlement
        fields = (
            "id",
            "collected_by",
            "court",
            "notes",
        )
        read_only_fields = ("id",)
        extra_kwargs = {"notes": {"required": False, "allow_blank": True}}

    def validate(self, attrs):
        context = self.context.get("access_context") or self.context.get("club_access")
        club = getattr(context, "club", None)
        court = attrs.get("court")
        if court is not None and club is not None and court.club_id != club.id:
            raise serializers.ValidationError(
                {
                    "court": [
                        serializers.ErrorDetail(
                            _("Court must belong to the selected club."),
                            code="invalid_court",
                        ),
                    ],
                }
            )
        target_collector = attrs.get("collected_by")
        validate_settlement_authority(
            context=context,
            collector=target_collector,
        )
        return attrs

    def create(self, validated_data):
        context = self.context.get("access_context") or self.context.get("club_access")
        return create_settlement(
            club=context.club,
            collected_by=validated_data.get("collected_by"),
            settled_by=context.profile,
            notes=validated_data.get("notes", ""),
            court=validated_data.get("court"),
        )

    def to_representation(self, instance):
        return SettlementDetailSerializer(instance, context=self.context).data


class SettlementPreviewRequestSerializer(serializers.Serializer):
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=Profile.objects.all(),
        required=False,
        allow_null=True,
        help_text="Collector Profile ID stored in Transaction.created_by.",
    )
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError

    def validate(self, attrs):
        context = self.context.get("access_context") or self.context.get("club_access")
        club = getattr(context, "club", None)
        court = attrs.get("court")
        if court is not None and club is not None and court.club_id != club.id:
            raise serializers.ValidationError(
                {
                    "court": [
                        serializers.ErrorDetail(
                            _("Court must belong to the selected club."),
                            code="invalid_court",
                        ),
                    ],
                }
            )
        target_collector = attrs.get("collected_by")
        if target_collector is None and "collected_by" not in self.initial_data:
            target_collector = context.profile
            attrs["collected_by"] = target_collector
        validate_preview_authority(
            context=context,
            collector=target_collector,
        )
        return attrs

    def preview(self):
        context = self.context.get("access_context") or self.context.get("club_access")
        target_collector = self.validated_data.get("collected_by")
        court = self.validated_data.get("court")
        data = preview_settlement(
            club=context.club,
            collected_by=target_collector,
            court=court,
        )
        can_approve = (
            can_approve_collector(context, target_collector.id)
            if target_collector
            else can_manage_settlements(context)
        )
        data["is_self_preview"] = bool(
            context.profile
            and target_collector
            and target_collector.id == context.profile.id
        )
        data["can_approve"] = can_approve
        data["approval_required"] = not can_approve
        return data


class SettlementPreviewTransactionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    booking = serializers.IntegerField(allow_null=True)
    booking_customer_name = serializers.CharField()
    booking_customer_phone = PhoneNumberField(allow_blank=True)
    booking_start_time = serializers.DateTimeField()
    booking_end_time = serializers.DateTimeField()
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    payment_method = serializers.CharField()
    payment_reference = serializers.CharField(allow_blank=True)
    created = serializers.DateTimeField()


class SettlementPreviewResponseSerializer(serializers.Serializer):
    club = serializers.IntegerField()
    collected_by = serializers.IntegerField(
        allow_null=True,
        help_text="Collector Profile ID stored in Transaction.created_by.",
    )
    collected_by_name = serializers.CharField(allow_blank=True)
    court = serializers.IntegerField(allow_null=True)
    court_name = serializers.CharField(allow_blank=True)
    is_self_preview = serializers.BooleanField()
    can_approve = serializers.BooleanField()
    approval_required = serializers.BooleanField()
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    net_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    booking_payments = serializers.DecimalField(max_digits=10, decimal_places=2)
    booking_refunds = serializers.DecimalField(max_digits=10, decimal_places=2)
    totals_by_payment_method = serializers.DictField(
        child=serializers.DecimalField(max_digits=10, decimal_places=2)
    )
    transactions = SettlementPreviewTransactionSerializer(many=True)


class SettlementUnsettledSummaryRequestSerializer(serializers.Serializer):
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=Profile.objects.all(),
        required=False,
        allow_null=True,
        help_text="Collector Profile ID stored in Transaction.created_by.",
    )
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError

    def validate(self, attrs):
        context = self.context.get("access_context") or self.context.get("club_access")
        club = getattr(context, "club", None)
        court = attrs.get("court")
        if court is not None and club is not None and court.club_id != club.id:
            raise serializers.ValidationError(
                {
                    "court": [
                        serializers.ErrorDetail(
                            _("Court must belong to the selected club."),
                            code="invalid_court",
                        ),
                    ],
                }
            )
        validate_unsettled_summary_authority(
            context=context,
            collector=attrs.get("collected_by"),
        )
        return attrs

    def get_summary(self):
        context = self.context.get("access_context") or self.context.get("club_access")
        target_collector = self.validated_data.get("collected_by")
        court = self.validated_data.get("court")
        queryset = Transaction.objects.filter(
            club=context.club,
            settlement_line__isnull=True,
            is_cancelled=False,
        )
        if target_collector is not None:
            queryset = queryset.filter(created_by=target_collector)
        if court is not None:
            queryset = queryset.filter(court=court)

        grouped = {}
        for t in queryset.select_related("created_by", "created_by__user").order_by(
            "created", "id"
        ):
            c = t.created_by
            c_id = c.id if c else None
            if c_id not in grouped:
                grouped[c_id] = {
                    "collector": c,
                    "transactions": [],
                }
            grouped[c_id]["transactions"].append(t)

        now = timezone.now()
        collector_ids = list(grouped.keys())
        roles_by_collector_id = get_active_profile_roles_by_id(
            context.club, collector_ids
        )
        results = []
        for c_id, data in grouped.items():
            roles = roles_by_collector_id.get(c_id, set())
            if not can_view_collector_in_summary(context, c_id, roles):
                continue
            txs = data["transactions"]
            payments = sum(
                (
                    tx.amount
                    for tx in txs
                    if tx.transaction_type == Transaction.Type.PAYMENT
                ),
                ZERO,
            )
            refunds = sum(
                (
                    tx.amount
                    for tx in txs
                    if tx.transaction_type == Transaction.Type.REFUND
                ),
                ZERO,
            )
            net = payments + refunds
            method_totals = {str(m): ZERO for m, _ in Transaction.PaymentMethod.choices}
            for tx in txs:
                method_totals[str(tx.payment_method)] += tx.amount

            name = profile_display_name(data["collector"])
            results.append(
                {
                    "collected_by": c_id,
                    "collected_by_name": name,
                    "period_start": txs[0].created,
                    "period_end": now,
                    "transaction_count": len(txs),
                    "total_amount": net,
                    "net_amount": net,
                    "booking_payments": payments,
                    "booking_refunds": refunds,
                    "totals_by_payment_method": method_totals,
                    "is_self": bool(context.profile and c_id == context.profile.id),
                    "can_approve": can_approve_collector(context, c_id, roles),
                }
            )

        results.sort(
            key=lambda item: (
                item["collected_by_name"].casefold(),
                item["collected_by"],
            )
        )
        return {"results": results}


class SettlementUnsettledSummaryRowSerializer(serializers.Serializer):
    collected_by = serializers.IntegerField(
        help_text="Collector Profile ID stored in Transaction.created_by."
    )
    collected_by_name = serializers.CharField()
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    net_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    booking_payments = serializers.DecimalField(max_digits=10, decimal_places=2)
    booking_refunds = serializers.DecimalField(max_digits=10, decimal_places=2)
    totals_by_payment_method = serializers.DictField(
        child=serializers.DecimalField(max_digits=10, decimal_places=2)
    )
    is_self = serializers.BooleanField()
    can_approve = serializers.BooleanField()


class SettlementUnsettledSummaryResponseSerializer(serializers.Serializer):
    results = SettlementUnsettledSummaryRowSerializer(many=True)
