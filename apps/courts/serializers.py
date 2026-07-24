from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.courts.services import (
    get_court_pricing_summary,
    pricing_configured_for_court,
    validate_slot_duration_against_pricing,
)


def validate_positive(value, field_name):
    if value <= 0:
        raise serializers.ValidationError({field_name: "Must be greater than 0."})


class CourtWorkingHourPricePeriodSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        model = CourtWorkingHourPricePeriod
        fields = (
            "id",
            "starts_at",
            "ends_at",
            "price",
        )
        read_only_fields = ("id",)


class CourtWorkingHourSerializer(serializers.ModelSerializer):
    pricing_periods = CourtWorkingHourPricePeriodSerializer(many=True, read_only=True)

    class Meta:
        model = CourtWorkingHour
        fields = (
            "id",
            "court",
            "weekday",
            "opens_at",
            "closes_at",
            "is_closed",
            "pricing_periods",
        )
        read_only_fields = ("id", "pricing_periods")

    def validate(self, attrs):
        access = self.context["club_access"]
        court = attrs.get("court", getattr(self.instance, "court", None))
        weekday = attrs.get("weekday", getattr(self.instance, "weekday", None))
        opens_at = attrs.get("opens_at", getattr(self.instance, "opens_at", None))
        closes_at = attrs.get(
            "closes_at",
            getattr(self.instance, "closes_at", None),
        )
        is_closed = attrs.get(
            "is_closed",
            getattr(self.instance, "is_closed", False),
        )

        if (
            self.instance is not None
            and "court" in attrs
            and attrs["court"] != self.instance.court
        ):
            raise serializers.ValidationError(
                {"court": "Existing working hours cannot change court."}
            )
        if court.club_id != access.club.id:
            raise serializers.ValidationError(
                {"court": "Court must belong to the selected club."}
            )
        if not access.can_manage_working_hours(court):
            raise PermissionDenied("You cannot manage working hours for this court.")

        if not is_closed:
            if opens_at is None or closes_at is None:
                raise serializers.ValidationError(
                    "Open working hours require both opens_at and closes_at."
                )
            if opens_at >= closes_at:
                raise serializers.ValidationError(
                    {"opens_at": "opens_at must be before closes_at."}
                )

        duplicate = CourtWorkingHour.objects.filter(court=court, weekday=weekday)
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError(
                "Working hours already exist for this court and weekday."
            )

        return attrs


class CourtWorkingHourNestedRowSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True, allow_null=True)
    weekday = serializers.ChoiceField(choices=CourtWorkingHour.Weekday.choices)
    opens_at = serializers.TimeField(required=False, allow_null=True)
    closes_at = serializers.TimeField(required=False, allow_null=True)
    is_closed = serializers.BooleanField(default=False)
    pricing_periods = CourtWorkingHourPricePeriodSerializer(many=True, required=False)

    def validate(self, attrs):
        is_closed = attrs.get("is_closed", False)
        opens_at = attrs.get("opens_at")
        closes_at = attrs.get("closes_at")

        if is_closed:
            if opens_at is not None or closes_at is not None:
                raise serializers.ValidationError(
                    "Closed working hours must not include opens_at or closes_at."
                )
            return attrs

        if opens_at is None or closes_at is None:
            raise serializers.ValidationError(
                "Open working hours require both opens_at and closes_at."
            )
        if opens_at >= closes_at:
            raise serializers.ValidationError(
                {"opens_at": "opens_at must be before closes_at."}
            )
        return attrs


class CourtWeeklyWorkingHoursSerializer(serializers.Serializer):
    court = serializers.IntegerField(read_only=True)
    court_name = serializers.CharField(read_only=True)
    pricing_configured = serializers.BooleanField(read_only=True)
    working_hours = CourtWorkingHourNestedRowSerializer(many=True)

    def validate_working_hours(self, value):
        weekdays = [row["weekday"] for row in value]
        if len(weekdays) != len(set(weekdays)):
            raise serializers.ValidationError("Duplicate weekdays are not allowed.")
        return value


class CourtListSerializer(serializers.ModelSerializer):
    pricing_configured = serializers.SerializerMethodField()
    minimum_slot_price = serializers.SerializerMethodField()
    maximum_slot_price = serializers.SerializerMethodField()

    class Meta:
        model = Court
        fields = (
            "id",
            "club",
            "name",
            "sport_type",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "pricing_configured",
            "minimum_slot_price",
            "maximum_slot_price",
        )
        read_only_fields = fields

    def get_pricing_configured(self, obj):
        return pricing_configured_for_court(obj)

    def get_minimum_slot_price(self, obj):
        return get_court_pricing_summary(obj)["minimum_slot_price"]

    def get_maximum_slot_price(self, obj):
        return get_court_pricing_summary(obj)["maximum_slot_price"]


class CourtDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    working_hours = CourtWorkingHourSerializer(many=True, read_only=True)
    pricing_configured = serializers.SerializerMethodField()
    minimum_slot_price = serializers.SerializerMethodField()
    maximum_slot_price = serializers.SerializerMethodField()

    class Meta:
        model = Court
        fields = (
            "id",
            "club",
            "name",
            "sport_type",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "pricing_configured",
            "minimum_slot_price",
            "maximum_slot_price",
            "notes",
            "created_by",
            "created",
            "modified",
            "working_hours",
        )
        read_only_fields = fields

    def get_pricing_configured(self, obj):
        return pricing_configured_for_court(obj)

    def get_minimum_slot_price(self, obj):
        return get_court_pricing_summary(obj)["minimum_slot_price"]

    def get_maximum_slot_price(self, obj):
        return get_court_pricing_summary(obj)["maximum_slot_price"]


class CourtCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = (
            "id",
            "name",
            "sport_type",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "notes",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        access = self.context["club_access"]

        if "default_price" in self.initial_data:
            raise serializers.ValidationError(
                {"default_price": _("Court default_price is deprecated.")}
            )
        validate_positive(
            attrs.get("slot_duration_minutes", 60),
            "slot_duration_minutes",
        )
        validate_positive(
            attrs.get("internal_hold_expiry_hours", 12),
            "internal_hold_expiry_hours",
        )

        if not access.can_create_court():
            raise PermissionDenied("You cannot create courts for this club.")

        return attrs

    def to_representation(self, instance):
        return CourtDetailSerializer(instance, context=self.context).data


class CourtUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = (
            "name",
            "sport_type",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "notes",
        )

    def validate(self, attrs):
        access = self.context["club_access"]
        court = self.instance

        if "default_price" in self.initial_data:
            raise serializers.ValidationError(
                {"default_price": _("Court default_price is deprecated.")}
            )
        if "slot_duration_minutes" in attrs:
            validate_positive(attrs["slot_duration_minutes"], "slot_duration_minutes")
            validate_slot_duration_against_pricing(
                court,
                attrs["slot_duration_minutes"],
            )
        if "internal_hold_expiry_hours" in attrs:
            validate_positive(
                attrs["internal_hold_expiry_hours"],
                "internal_hold_expiry_hours",
            )

        if not access.can_update_court(court, attrs):
            raise PermissionDenied("You cannot update this court.")

        return attrs

    def to_representation(self, instance):
        return CourtDetailSerializer(instance, context=self.context).data
