from rest_framework import serializers

from apps.courts.models import Court
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import create_settlement, preview_settlement


class SettlementListSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "period_start",
            "period_end",
            "status",
            "total_amount",
            "transaction_count",
            "created_by",
            "settled_by",
            "settled_at",
            "created",
        )
        read_only_fields = fields


class SettlementLineSerializer(serializers.ModelSerializer):
    transaction = serializers.PrimaryKeyRelatedField(read_only=True)
    booking = serializers.IntegerField(source="transaction.booking_id", read_only=True)
    court = serializers.IntegerField(source="transaction.court_id", read_only=True)
    payment_method = serializers.CharField(
        source="transaction.payment_method",
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

    class Meta:
        model = SettlementTransaction
        fields = (
            "id",
            "transaction",
            "booking",
            "court",
            "amount",
            "payment_method",
            "payment_reference",
            "transaction_created",
        )
        read_only_fields = fields


class SettlementDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    lines = SettlementLineSerializer(many=True, read_only=True)

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "period_start",
            "period_end",
            "status",
            "total_amount",
            "transaction_count",
            "notes",
            "created_by",
            "settled_by",
            "settled_at",
            "created",
            "modified",
            "lines",
        )
        read_only_fields = fields


class SettlementCreateSerializer(serializers.ModelSerializer):
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Settlement
        fields = (
            "id",
            "court",
            "period_start",
            "period_end",
            "notes",
        )
        read_only_fields = ("id",)
        extra_kwargs = {"notes": {"required": False, "allow_blank": True}}

    def create(self, validated_data):
        request = self.context["request"]
        return create_settlement(
            access=self.context["club_access"],
            created_by=request.user,
            **validated_data,
        )

    def to_representation(self, instance):
        return SettlementDetailSerializer(instance, context=self.context).data


class SettlementPreviewRequestSerializer(serializers.Serializer):
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError

    def preview(self):
        return preview_settlement(
            access=self.context["club_access"],
            **self.validated_data,
        )


class SettlementPreviewResponseSerializer(serializers.Serializer):
    club = serializers.IntegerField()
    court = serializers.IntegerField(allow_null=True)
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
