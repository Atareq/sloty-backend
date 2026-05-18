from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.clubs.permissions import is_active_club_manager, is_active_club_owner
from apps.courts.models import Court, CourtStaffAssignment, CourtWorkingHour
from apps.courts.permissions import can_manage_court_setup, can_manage_working_hours


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
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
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

        if not can_manage_working_hours(request_user, court):
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
        read_only_fields = ("id",)


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
        read_only_fields = ("id", "created_by", "created", "modified")


class CourtCreateSerializer(serializers.ModelSerializer):
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
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        club = attrs["club"]

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

        if not (
            request_user.is_platform_super_admin()
            or is_active_club_owner(request_user, club)
        ):
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
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
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

        if can_manage_court_setup(request_user, court):
            return attrs

        if is_active_club_manager(request_user, court.club):
            if set(attrs) != {"default_price"}:
                raise PermissionDenied(
                    "Managers can update only court pricing in Sprint 2."
                )
            if not court.club.manager_can_change_pricing:
                raise PermissionDenied(
                    "This club does not allow managers to change pricing."
                )
            return attrs

        raise PermissionDenied("You cannot update this court.")

    def to_representation(self, instance):
        return CourtDetailSerializer(instance, context=self.context).data


class CourtStaffAssignmentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = CourtStaffAssignment
        fields = (
            "id",
            "court",
            "user",
            "is_active",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created_by", "created", "modified")

    def validate(self, attrs):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        court = attrs.get("court", getattr(self.instance, "court", None))
        user = attrs.get("user", getattr(self.instance, "user", None))
        is_active = attrs.get(
            "is_active",
            getattr(self.instance, "is_active", True),
        )

        if self.instance is not None:
            for field_name in ("court", "user"):
                if field_name in attrs and attrs[field_name] != getattr(
                    self.instance, field_name
                ):
                    raise serializers.ValidationError(
                        {field_name: "Existing staff assignments cannot change scope."}
                    )

        if not (
            request_user.is_platform_super_admin()
            or is_active_club_owner(request_user, court.club)
        ):
            raise PermissionDenied("You cannot manage staff for this court.")

        if user.role != User.Role.STAFF:
            raise serializers.ValidationError(
                {"user": "Court staff assignments require a STAFF user."}
            )

        if is_active:
            duplicate_assignment = CourtStaffAssignment.objects.filter(
                court=court,
                user=user,
                is_active=True,
            )
            active_user_assignment = CourtStaffAssignment.objects.filter(
                user=user,
                is_active=True,
            )
            if self.instance is not None:
                duplicate_assignment = duplicate_assignment.exclude(pk=self.instance.pk)
                active_user_assignment = active_user_assignment.exclude(
                    pk=self.instance.pk
                )
            if duplicate_assignment.exists():
                raise serializers.ValidationError(
                    "This active court staff assignment already exists."
                )
            if active_user_assignment.exists():
                raise serializers.ValidationError(
                    {"user": "A staff user can have only one active court assignment."}
                )

        return attrs
