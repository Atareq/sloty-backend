from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers, status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.profiles.models import Profile


def get_token_name(user):
    return user.get_full_name() or user.username


def get_profile_for_token_context(*, user, club):
    profile = getattr(user, "profile", None)
    if profile is None:
        return None
    if profile.role == Profile.Role.ADMIN:
        return profile
    if (
        profile.role == Profile.Role.OWNER
        and profile.owner_profile.clubs.filter(pk=club.pk).exists()
    ):
        return profile
    if (
        profile.role == Profile.Role.STAFF
        and profile.staff_profile.court.club_id == club.pk
    ):
        return profile
    return None


def build_token_claims(*, user, club_slug=None):
    claims = {
        "user_id": user.id,
        "role": "",
        "name": get_token_name(user),
    }

    profile = getattr(user, "profile", None)
    if profile is not None:
        claims["role"] = profile.role

    if not club_slug:
        return claims

    try:
        club = Club.objects.get(slug=club_slug)
    except Club.DoesNotExist as exc:
        raise serializers.ValidationError(
            {"club_slug": "Invalid club context."}
        ) from exc

    if profile and profile.role == Profile.Role.ADMIN:
        claims["club_id"] = club.id
        return claims

    profile = get_profile_for_token_context(user=user, club=club)
    if profile is None:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message="Your access to the selected club is no longer active.",
            details={
                "club_slug": club.slug,
            },
        )

    claims["role"] = profile.role
    claims["club_id"] = club.id
    if profile.role == Profile.Role.STAFF:
        claims["court_id"] = profile.staff_profile.court_id
    return claims


class SlotyTokenObtainPairSerializer(TokenObtainPairSerializer):
    club_slug = serializers.SlugField(required=False, allow_blank=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        for key, value in build_token_claims(user=user).items():
            token[key] = value
        return token

    def validate(self, attrs):
        club_slug = attrs.pop("club_slug", "")
        super().validate(attrs)

        claims = build_token_claims(user=self.user, club_slug=club_slug or None)
        refresh = self.get_token(self.user)
        for key, value in claims.items():
            refresh[key] = value

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }


class SlotyTokenRefreshSerializer(TokenRefreshSerializer):
    """Preserve SimpleJWT refresh behavior while revalidating the user state."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        JWTAuthentication().get_user(refresh)
        return super().validate(attrs)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirmation = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirmation"]:
            raise serializers.ValidationError(
                {"new_password_confirmation": "Passwords do not match."}
            )

        try:
            validate_password(attrs["new_password"], self.context["request"].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages}) from exc

        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class UserMembershipClubSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.SlugField()
    name = serializers.CharField()


class UserMembershipCourtSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserMembershipPermissionsSerializer(serializers.Serializer):
    can_change_pricing = serializers.BooleanField()
    can_manage_working_hours = serializers.BooleanField()
    can_manage_settlements = serializers.BooleanField()


class UserMembershipSerializer(serializers.Serializer):
    club = UserMembershipClubSerializer(read_only=True)
    court = UserMembershipCourtSerializer(read_only=True)
    permissions = serializers.SerializerMethodField()

    @extend_schema_field(UserMembershipPermissionsSerializer)
    def get_permissions(self, profile):
        if profile.role == Profile.Role.OWNER:
            permissions = {
                "can_change_pricing": True,
                "can_manage_working_hours": True,
                "can_manage_settlements": True,
            }
        else:
            permissions = {
                "can_change_pricing": False,
                "can_manage_working_hours": False,
                "can_manage_settlements": False,
            }
        return UserMembershipPermissionsSerializer(permissions).data


class SyncHeartbeatResponseSerializer(serializers.Serializer):
    """
    Response for the explicit offline/PWA sync heartbeat endpoint
    (POST /api/v1/me/sync-heartbeat/). The timestamp always originates from
    the server clock; clients never supply it.
    """

    last_sync_at = serializers.DateTimeField(read_only=True)


class AccountCreatorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserMeSerializer(serializers.ModelSerializer):
    account_created_by = serializers.SerializerMethodField()
    profile_role = serializers.CharField(
        source="profile.role", read_only=True, allow_null=True
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "is_active",
            "profile_role",
            "account_created_by",
        )
        read_only_fields = fields

    @extend_schema_field(AccountCreatorSerializer(allow_null=True))
    def get_account_created_by(self, obj):
        creator = obj.created_by
        if creator is None:
            return None
        return AccountCreatorSerializer(
            {
                "id": creator.id,
                "name": get_token_name(creator),
            }
        ).data


class UserListSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "is_active",
            "created_by",
        )
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    is_platform_admin = serializers.BooleanField(
        write_only=True, required=False, default=False
    )
    non_platform_user_error = (
        "Club users must be created through a club-scoped membership endpoint."
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "password",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "is_active",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "username": {"required": True},
        }

    def validate(self, attrs):
        forbidden_fields = {"role", "club", "court", "membership", "memberships"}
        submitted_forbidden_fields = forbidden_fields.intersection(self.initial_data)
        if submitted_forbidden_fields:
            raise serializers.ValidationError(
                {
                    field_name: "This field is not accepted by this endpoint."
                    for field_name in sorted(submitted_forbidden_fields)
                }
            )
        if not attrs.get("is_platform_admin"):
            raise serializers.ValidationError(
                {
                    "non_field_errors": [
                        serializers.ErrorDetail(
                            self.non_platform_user_error,
                            code="invalid",
                        )
                    ]
                }
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        is_platform_admin = validated_data.pop("is_platform_admin", False)
        user = User.objects.create_user(
            password=password,
            is_staff=False,
            is_superuser=False,
            **validated_data,
        )
        if is_platform_admin:
            profile = Profile.objects.create(user=user, role=Profile.Role.ADMIN)
            from apps.profiles.models import AdminProfile

            AdminProfile.objects.create(profile=profile)
        return user

    def to_representation(self, instance):
        return UserListSerializer(instance, context=self.context).data


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "is_active",
        )

    def to_representation(self, instance):
        return UserListSerializer(instance, context=self.context).data
