from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
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
from apps.common.captcha import captcha_enabled, verify_captcha
from apps.profiles.models import Profile


def get_token_name(user):
    return user.get_full_name() or user.username


def get_profile_for_token_context(*, user, club):
    profile = getattr(user, "profile", None)
    if profile is None or not profile.is_enabled:
        return None
    if profile.role == Profile.Role.ADMIN:
        return profile
    if (
        profile.role == Profile.Role.OWNER
        and getattr(profile, "owner_profile", None)
        and profile.owner_profile.clubs.filter(pk=club.pk).exists()
    ):
        return profile
    staff_profile = getattr(profile, "staff_profile", None)
    if (
        profile.role == Profile.Role.STAFF
        and staff_profile is not None
        and staff_profile.club_id == club.pk
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
    if profile is not None and profile.is_enabled:
        claims["role"] = profile.role

    if not club_slug:
        return claims

    try:
        club = Club.objects.get(slug=club_slug)
    except Club.DoesNotExist as exc:
        raise serializers.ValidationError(
            {"club_slug": "Invalid club context."}
        ) from exc

    if profile and profile.is_enabled and profile.role == Profile.Role.ADMIN:
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
        court_ids = list(
            profile.staff_profile.court_accesses.values_list("court_id", flat=True)
        )
        if len(court_ids) == 1:
            claims["court_id"] = court_ids[0]
    return claims


class SlotyTokenObtainPairSerializer(TokenObtainPairSerializer):
    club_slug = serializers.SlugField(required=False, allow_blank=True)
    # Write-only: the token is proof for the provider, never part of the
    # response and never handed to a service. The field is declared
    # unconditionally so the OpenAPI schema is stable, but it is only read when
    # CAPTCHA_ENABLED is true.
    captcha_token = serializers.CharField(
        required=False, allow_blank=True, write_only=True
    )

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        for key, value in build_token_claims(user=user).items():
            token[key] = value
        return token

    def validate(self, attrs):
        club_slug = attrs.pop("club_slug", "")
        captcha_token = attrs.pop("captcha_token", "")

        # Verified before the credential check so a failed CAPTCHA costs an
        # attacker a password guess without telling them anything about the
        # account. Disabled deployments skip this entirely.
        if captcha_enabled():
            request = self.context.get("request")
            verify_captcha(
                captcha_token,
                remote_ip=request.META.get("REMOTE_ADDR") if request else None,
            )

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
            "created_by",
        )
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

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
        forbidden_fields = {
            "role",
            "club",
            "court",
            "membership",
            "memberships",
        }
        submitted_forbidden_fields = forbidden_fields.intersection(self.initial_data)
        if submitted_forbidden_fields:
            raise serializers.ValidationError(
                {
                    field_name: "This field is not accepted by this endpoint."
                    for field_name in sorted(submitted_forbidden_fields)
                }
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        from apps.profiles.models import AdminProfile

        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            is_staff=False,
            is_superuser=False,
            **validated_data,
        )
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)
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
