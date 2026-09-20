from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.profiles.models import OwnerProfile, Profile


class OwnerClubSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = ("id", "name", "slug")
        read_only_fields = fields


class OwnerDetailSerializer(serializers.ModelSerializer):
    profile_id = serializers.IntegerField(source="pk", read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    is_enabled = serializers.BooleanField(read_only=True)
    role = serializers.CharField(read_only=True)
    club_ids = serializers.SerializerMethodField()
    clubs = OwnerClubSummarySerializer(many=True, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created = serializers.DateTimeField(read_only=True)

    class Meta:
        model = OwnerProfile
        fields = (
            "id",
            "profile_id",
            "user_id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "is_active",
            "is_enabled",
            "role",
            "club_ids",
            "clubs",
            "created_by",
            "created",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_club_ids(self, obj):
        return list(obj.clubs.values_list("id", flat=True))


class OwnerListSerializer(OwnerDetailSerializer):
    pass


class OwnerCreateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(
        max_length=150,
        required=True,
        validators=[UnicodeUsernameValidator()],
    )
    password = serializers.CharField(write_only=True, required=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    first_name = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default=""
    )
    last_name = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default=""
    )
    phone_number = PhoneNumberField(required=False, allow_null=True)
    club_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False,
        help_text="List of club IDs to assign to this owner.",
    )

    class Meta:
        model = OwnerProfile
        fields = (
            "username",
            "password",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "club_ids",
        )

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with that username already exists."
            )
        return value

    def validate_club_ids(self, value):
        if not value:
            raise serializers.ValidationError("At least one club must be assigned.")
        found_clubs = list(Club.objects.filter(pk__in=value))
        found_ids = {c.id for c in found_clubs}
        missing_ids = set(value) - found_ids
        if missing_ids:
            raise serializers.ValidationError(
                f"Clubs with IDs {sorted(missing_ids)} do not exist."
            )
        return list(dict.fromkeys(value))

    def create(self, validated_data):
        access = self.context.get("access_context")
        if access is None or access.profile is None:
            raise serializers.ValidationError("Admin access context is required.")
        if access.profile.role != Profile.Role.ADMIN:
            raise serializers.ValidationError("Only an Admin can create an Owner.")

        club_ids = validated_data.pop("club_ids")
        password = validated_data.pop("password")
        clubs = list(Club.objects.filter(id__in=club_ids))

        with transaction.atomic():
            user = User.objects.create_user(
                password=password,
                is_staff=False,
                is_superuser=False,
                **validated_data,
            )
            owner_profile = OwnerProfile.objects.create(
                user=user,
                role=Profile.Role.OWNER,
                created_by=access.profile,
            )
            owner_profile.clubs.set(clubs)
            return owner_profile

    def to_representation(self, instance):
        return OwnerDetailSerializer(instance, context=self.context).data
