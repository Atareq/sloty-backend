from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.courts.models import Court, CourtWorkingHour


def validate_positive(value, field_name):
    if value <= 0:
        raise serializers.ValidationError({field_name: "Must be greater than 0."})


class CourtWorkingHourSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourtWorkingHour
        fields = (
            "id",
            "court",
            "weekday",
            "opens_at",
            "closes_at",
            "is_closed",
        )
        read_only_fields = ("id",)

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


class CourtListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = (
            "id",
            "club",
            "name",
            "sport_type",
            "default_price",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
        )
        read_only_fields = fields


class CourtDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    working_hours = CourtWorkingHourSerializer(many=True, read_only=True)

    class Meta:
        model = Court
        fields = (
            "id",
            "club",
            "name",
            "sport_type",
            "default_price",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "notes",
            "created_by",
            "created",
            "modified",
            "working_hours",
        )
        read_only_fields = fields


class CourtCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = (
            "id",
            "name",
            "sport_type",
            "default_price",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "notes",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        access = self.context["club_access"]

        if attrs.get("default_price", Decimal("0.00")) < Decimal("0.00"):
            raise serializers.ValidationError(
                {"default_price": "Must be greater than or equal to 0."}
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
            "default_price",
            "slot_duration_minutes",
            "is_active",
            "requires_digital_payment_reference",
            "internal_hold_expiry_hours",
            "notes",
        )

    def validate(self, attrs):
        access = self.context["club_access"]
        court = self.instance

        if "default_price" in attrs and attrs["default_price"] < Decimal("0.00"):
            raise serializers.ValidationError(
                {"default_price": "Must be greater than or equal to 0."}
            )
        if "slot_duration_minutes" in attrs:
            validate_positive(attrs["slot_duration_minutes"], "slot_duration_minutes")
        if "internal_hold_expiry_hours" in attrs:
            validate_positive(
                attrs["internal_hold_expiry_hours"],
                "internal_hold_expiry_hours",
            )

        if not access.can_update_court(court, attrs):
            if access.is_manager and set(attrs) == {"default_price"}:
                raise PermissionDenied(
                    "This club does not allow managers to change pricing."
                )
            raise PermissionDenied("You cannot update this court.")

        return attrs

    def to_representation(self, instance):
        return CourtDetailSerializer(instance, context=self.context).data
