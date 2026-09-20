from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from apps.accounts.models import User
from apps.courts.models import Court
from apps.profiles.models import Profile, StaffCourtAccess, StaffProfile


class StaffCourtSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = ("id", "name", "is_active")
        read_only_fields = fields


class StaffDetailSerializer(serializers.ModelSerializer):
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
    club = serializers.PrimaryKeyRelatedField(read_only=True)
    court_ids = serializers.SerializerMethodField()
    courts = serializers.SerializerMethodField()
    created = serializers.DateTimeField(read_only=True)

    class Meta:
        model = StaffProfile
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
            "club",
            "court_ids",
            "courts",
            "created",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_court_ids(self, obj):
        return [ca.court_id for ca in obj.court_accesses.all()]

    @extend_schema_field(StaffCourtSummarySerializer(many=True))
    def get_courts(self, obj):
        return StaffCourtSummarySerializer(
            [ca.court for ca in obj.court_accesses.all()], many=True
        ).data


class StaffListSerializer(StaffDetailSerializer):
    pass


class StaffCourtAssignmentSerializer(serializers.Serializer):
    court_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=True,
        help_text="Complete list of court IDs to assign to this staff member.",
    )

    def validate_court_ids(self, value):
        access = self.context.get("access_context")
        if access is None or getattr(access, "club", None) is None:
            raise serializers.ValidationError("Club context is required.")
        club = access.club
        if not value:
            return value
        found_courts = list(Court.objects.filter(pk__in=value))
        found_ids = {c.id for c in found_courts}
        missing_ids = set(value) - found_ids
        if missing_ids:
            raise serializers.ValidationError(
                f"Courts with IDs {sorted(missing_ids)} do not exist."
            )
        wrong_club = [c.id for c in found_courts if c.club_id != club.id]
        if wrong_club:
            raise serializers.ValidationError(
                f"Courts {sorted(wrong_club)} do not belong to club {club.slug}."
            )
        return list(dict.fromkeys(value))


class StaffCreateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(max_length=150, required=True)
    password = serializers.CharField(write_only=True, required=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    first_name = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default=""
    )
    last_name = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default=""
    )
    phone_number = PhoneNumberField(required=False, allow_null=True)
    court_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
        help_text="Optional list of court IDs within this club to assign.",
    )

    class Meta:
        model = StaffProfile
        fields = (
            "id",
            "username",
            "password",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "court_ids",
        )
        read_only_fields = ("id",)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError(
                "A user with that username already exists."
            )
        return value

    def validate_court_ids(self, value):
        access = self.context.get("access_context")
        if access is None or getattr(access, "club", None) is None:
            raise serializers.ValidationError("Club context is required.")
        club = access.club
        if not value:
            return value
        found_courts = list(Court.objects.filter(pk__in=value))
        found_ids = {c.id for c in found_courts}
        missing_ids = set(value) - found_ids
        if missing_ids:
            raise serializers.ValidationError(
                f"Courts with IDs {sorted(missing_ids)} do not exist."
            )
        wrong_club = [c.id for c in found_courts if c.club_id != club.id]
        if wrong_club:
            raise serializers.ValidationError(
                f"Courts {sorted(wrong_club)} do not belong to club {club.slug}."
            )
        return list(dict.fromkeys(value))

    def create(self, validated_data):
        access = self.context["access_context"]
        court_ids = validated_data.pop("court_ids", [])
        password = validated_data.pop("password")

        with transaction.atomic():
            user = User.objects.create_user(
                password=password,
                is_staff=False,
                is_superuser=False,
                **validated_data,
            )
            staff_profile = StaffProfile.objects.create(
                user=user,
                role=Profile.Role.STAFF,
                club=access.club,
                created_by=access.profile,
            )
            if court_ids:
                courts = Court.objects.filter(club=access.club, id__in=court_ids)
                for court in courts:
                    StaffCourtAccess.objects.create(
                        staff_profile=staff_profile,
                        court=court,
                    )
            return staff_profile

    def to_representation(self, instance):
        return StaffDetailSerializer(instance, context=self.context).data


class StaffUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False)
    phone_number = PhoneNumberField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    is_enabled = serializers.BooleanField(required=False)
    court_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Optional list of court IDs to reassign.",
    )

    def validate_court_ids(self, value):
        access = self.context.get("access_context")
        if access is None or getattr(access, "club", None) is None:
            raise serializers.ValidationError("Club context is required.")
        club = access.club
        if not value:
            return value
        found_courts = list(Court.objects.filter(pk__in=value))
        found_ids = {c.id for c in found_courts}
        missing_ids = set(value) - found_ids
        if missing_ids:
            raise serializers.ValidationError(
                f"Courts with IDs {sorted(missing_ids)} do not exist."
            )
        wrong_club = [c.id for c in found_courts if c.club_id != club.id]
        if wrong_club:
            raise serializers.ValidationError(
                f"Courts {sorted(wrong_club)} do not belong to club {club.slug}."
            )
        return list(dict.fromkeys(value))

    def update(self, instance, validated_data):
        user = instance.user
        user_fields = ["first_name", "last_name", "email", "phone_number", "is_active"]
        user_updated = False

        with transaction.atomic():
            instance = StaffProfile.objects.select_for_update().get(pk=instance.pk)
            for field in user_fields:
                if field in validated_data:
                    setattr(user, field, validated_data[field])
                    user_updated = True
            if user_updated:
                user.save()

            if "is_enabled" in validated_data:
                instance.is_enabled = validated_data["is_enabled"]
                instance.save(update_fields=["is_enabled"])

            if "court_ids" in validated_data:
                court_ids = validated_data["court_ids"]
                instance.court_accesses.all().delete()
                for court_id in court_ids:
                    StaffCourtAccess.objects.create(
                        staff_profile=instance,
                        court_id=court_id,
                    )

            return instance

    def to_representation(self, instance):
        return StaffDetailSerializer(instance, context=self.context).data
