from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.bookings.services import create_booking, validate_booking_duration


class BookingListSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

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
            "status",
            "source",
            "created_by",
            "created",
        )
        read_only_fields = fields


class BookingDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

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
            "status",
            "source",
            "notes",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
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

        validate_booking_duration(court, attrs["start_time"], attrs["end_time"])
        attrs["source"] = source
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        court = validated_data.pop("court")
        start_time = validated_data.pop("start_time")
        end_time = validated_data.pop("end_time")
        return create_booking(
            created_by=request.user,
            court=court,
            start_time=start_time,
            end_time=end_time,
            **validated_data,
        )

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data


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

    def to_representation(self, instance):
        return BookingDetailSerializer(instance, context=self.context).data
