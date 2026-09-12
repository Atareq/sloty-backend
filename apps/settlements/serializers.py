from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.courts.models import Court
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import preview_custody, settle_custody


def user_display_name(user):
    if user is None:
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


class SettlementListSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    collected_by_name = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by_name = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "court_name",
            "period_start",
            "period_end",
            "status",
            "total_amount",
            "transaction_count",
            "collected_by",
            "collected_by_name",
            "created_by",
            "settled_by",
            "settled_by_name",
            "settled_at",
            "created",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_collected_by_name(self, obj):
        return user_display_name(obj.collected_by)

    @extend_schema_field(serializers.CharField())
    def get_settled_by_name(self, obj):
        return user_display_name(obj.settled_by)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_court_name(self, obj):
        if obj.court_id is None:
            return None
        return obj.court.name


class SettlementLineSerializer(serializers.ModelSerializer):
    transaction = serializers.PrimaryKeyRelatedField(read_only=True)
    booking = serializers.IntegerField(source="transaction.booking_id", read_only=True)
    booking_customer_name = serializers.CharField(
        source="transaction.booking.customer_name",
        read_only=True,
    )
    booking_customer_phone = serializers.CharField(
        source="transaction.booking.customer_phone",
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


class SettlementDetailSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    collected_by_name = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by_name = serializers.SerializerMethodField()
    court_name = serializers.SerializerMethodField()
    lines = SettlementLineSerializer(many=True, read_only=True)
    transactions = SettlementLineSerializer(source="lines", many=True, read_only=True)

    class Meta:
        model = Settlement
        fields = (
            "id",
            "club",
            "court",
            "court_name",
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
            "settled_by_name",
            "settled_at",
            "created",
            "modified",
            "lines",
            "transactions",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_collected_by_name(self, obj):
        return user_display_name(obj.collected_by)

    @extend_schema_field(serializers.CharField())
    def get_settled_by_name(self, obj):
        return user_display_name(obj.settled_by)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_court_name(self, obj):
        if obj.court_id is None:
            return None
        return obj.court.name


class SettlementCreateSerializer(serializers.ModelSerializer):
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=True,
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
        access = self.context["club_access"]
        court = attrs.get("court")
        if court is not None and court.club_id != access.club.id:
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
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return settle_custody(
            access=self.context["club_access"],
            actor=request.user,
            collector=validated_data["collected_by"],
            court=validated_data.get("court"),
            notes=validated_data.get("notes", ""),
        )

    def to_representation(self, instance):
        return SettlementDetailSerializer(instance, context=self.context).data


class SettlementPreviewRequestSerializer(serializers.Serializer):
    collected_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
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
        access = self.context["club_access"]
        court = attrs.get("court")
        if court is not None and court.club_id != access.club.id:
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
        attrs.setdefault("collected_by", self.context["request"].user)
        return attrs

    def preview(self):
        request = self.context["request"]
        return preview_custody(
            access=self.context["club_access"],
            actor=request.user,
            collector=self.validated_data["collected_by"],
            court=self.validated_data.get("court"),
        )


class SettlementPreviewTransactionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    booking = serializers.IntegerField(allow_null=True)
    booking_customer_name = serializers.CharField()
    booking_customer_phone = serializers.CharField()
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
    collected_by = serializers.IntegerField()
    collected_by_name = serializers.CharField()
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
        queryset=User.objects.all(),
        required=False,
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
        access = self.context["club_access"]
        court = attrs.get("court")
        if court is not None and court.club_id != access.club.id:
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
        return attrs


class SettlementUnsettledSummaryRowSerializer(serializers.Serializer):
    collected_by = serializers.IntegerField()
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
