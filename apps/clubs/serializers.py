from rest_framework import serializers

from apps.clubs.models import Club, generate_unique_club_slug
from apps.common.egypt_locations import (
    get_all_city_choices,
    get_governorate_choices,
    is_valid_city_for_governorate,
)


class ClubLocationValidationMixin:
    governorate = serializers.ChoiceField(choices=get_governorate_choices())
    city = serializers.ChoiceField(choices=get_all_city_choices())

    def validate(self, attrs):
        attrs = super().validate(attrs)
        governorate = attrs.get(
            "governorate", getattr(self.instance, "governorate", None)
        )
        city = attrs.get("city", getattr(self.instance, "city", None))
        if (
            governorate
            and city
            and not is_valid_city_for_governorate(governorate, city)
        ):
            raise serializers.ValidationError(
                {"city": "City must belong to the selected governorate."}
            )
        return attrs


class ClubListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "slug",
            "governorate",
            "city",
            "phone_number",
            "is_active",
            "created",
            "modified",
        )


class ClubDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "slug",
            "governorate",
            "city",
            "address",
            "phone_number",
            "notes",
            "is_active",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created_by", "created", "modified")


class ClubCreateSerializer(ClubLocationValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "slug",
            "governorate",
            "city",
            "address",
            "phone_number",
            "notes",
            "is_active",
        )
        extra_kwargs = {"slug": {"required": False, "allow_blank": True}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("slug"):
            attrs["slug"] = generate_unique_club_slug(attrs["name"])
        return attrs


class ClubUpdateSerializer(ClubLocationValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "name",
            "governorate",
            "city",
            "address",
            "phone_number",
            "notes",
            "is_active",
        )
