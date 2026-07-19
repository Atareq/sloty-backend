from rest_framework import serializers

from apps.accounts.models import User
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import create_settlement, preview_settlement


class SettlementListSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    collected_by_name = serializers.SerializerMethodField()
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
            "collected_by",
            "collected_by_name",
            "created_by",
            "settled_by",
            "settled_at",
            "created",
        )
        read_only_fields = fields

    def get_collected_by_name(self, obj):
        if obj.collected_by is None:
            return ""
        full_name = obj.collected_by.get_full_name().strip()
        return full_name or obj.collected_by.username


class SettlementLineSerializer(serializers.ModelSerializer):
    transaction = serializers.PrimaryKeyRelatedField(read_only=True)
    booking = serializers.IntegerField(source="transaction.booking_id", read_only=True)
    court = serializers.IntegerField(source="transaction.court_id", read_only=True)
    court_name = serializers.CharField(source="transaction.court.name", read_only=True)
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
    created = serializers.DateTimeField(source="transaction.created", read_only=True)

    class Meta:
        model = SettlementTransaction
        fields = (
            "id",
            "transaction",
            "booking",
            "court",
            "court_name",
            "amount",
            "payment_method",
            "payment_reference",
            "transaction_created",
            "created",
        )
        read_only_fields = fields


class SettlementDetailSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    collected_by_name = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    lines = SettlementLineSerializer(many=True, read_only=True)
    transactions = SettlementLineSerializer(source="lines", many=True, read_only=True)

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
            "collected_by",
            "collected_by_name",
            "created_by",
            "settled_by",
            "settled_at",
            "created",
            "modified",
            "lines",
            "transactions",
        )
        read_only_fields = fields

    def get_collected_by_name(self, obj):
        if obj.collected_by is None:
            return ""
        full_name = obj.collected_by.get_full_name().strip()
        return full_name or obj.collected_by.username


class SettlementCreateSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = Settlement
        fields = (
            "id",
            "collected_by",
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
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
    )

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError

    def validate(self, attrs):
        attrs.setdefault("collected_by", self.context["request"].user)
        return attrs

    def preview(self):
        return preview_settlement(
            access=self.context["club_access"],
            actor=self.context["request"].user,
            **self.validated_data,
        )


class SettlementPreviewTransactionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    booking = serializers.IntegerField()
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    payment_method = serializers.CharField()
    payment_reference = serializers.CharField(allow_blank=True)
    created = serializers.DateTimeField()


class SettlementPreviewResponseSerializer(serializers.Serializer):
    club = serializers.IntegerField()
    collected_by = serializers.IntegerField()
    collected_by_name = serializers.CharField()
    is_self_preview = serializers.BooleanField()
    can_approve = serializers.BooleanField()
    approval_required = serializers.BooleanField()
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    transaction_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    totals_by_payment_method = serializers.DictField(
        child=serializers.DecimalField(max_digits=10, decimal_places=2)
    )
    transactions = SettlementPreviewTransactionSerializer(many=True)
