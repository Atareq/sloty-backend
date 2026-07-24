from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership


def get_token_name(user):
    return user.get_full_name() or user.username


def get_membership_for_token_context(*, user, club):
    role_order = {
        ClubMembership.Role.OWNER: 0,
        ClubMembership.Role.MANAGER: 1,
        ClubMembership.Role.STAFF: 2,
    }
    memberships = list(
        ClubMembership.objects.filter(
            user=user,
            club=club,
            is_active=True,
        )
        .select_related("court")
        .order_by("id")
    )
    if not memberships:
        return None
    return sorted(memberships, key=lambda item: role_order[item.role])[0]


def build_token_claims(*, user, club_slug=None):
    claims = {
        "user_id": user.id,
        "role": "",
        "name": get_token_name(user),
    }

    if user.is_platform_super_admin():
        claims["role"] = "PLATFORM_ADMIN"

    if not club_slug:
        return claims

    try:
        club = Club.objects.get(slug=club_slug)
    except Club.DoesNotExist as exc:
        raise serializers.ValidationError(
            {"club_slug": "Invalid club context."}
        ) from exc

    if user.is_platform_super_admin():
        claims["club_id"] = club.id
        return claims

    membership = get_membership_for_token_context(user=user, club=club)
    if membership is None:
        raise serializers.ValidationError(
            {"club_slug": "User has no active membership in this club."}
        )

    claims["role"] = membership.role
    claims["club_id"] = club.id
    if membership.role == ClubMembership.Role.STAFF and membership.court_id:
        claims["court_id"] = membership.court_id
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


class UserMembershipSerializer(serializers.ModelSerializer):
    club = UserMembershipClubSerializer(read_only=True)
    court = UserMembershipCourtSerializer(read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = ClubMembership
        fields = (
            "id",
            "role",
            "club",
            "court",
            "permissions",
        )

    def get_permissions(self, membership):
        if membership.role == ClubMembership.Role.OWNER:
            permissions = {
                "can_change_pricing": True,
                "can_manage_working_hours": True,
                "can_manage_settlements": True,
            }
        elif membership.role == ClubMembership.Role.MANAGER:
            permissions = {
                "can_change_pricing": membership.manager_can_change_pricing,
                "can_manage_working_hours": membership.manager_can_change_pricing,
                "can_manage_settlements": (membership.manager_can_settle_transactions),
            }
        else:
            permissions = {
                "can_change_pricing": False,
                "can_manage_working_hours": False,
                "can_manage_settlements": False,
            }
        return UserMembershipPermissionsSerializer(permissions).data


class AccountCreatorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserMeSerializer(serializers.ModelSerializer):
    account_created_by = serializers.SerializerMethodField()
    memberships = serializers.SerializerMethodField()

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
            "is_platform_admin",
            "account_created_by",
            "memberships",
        )
        read_only_fields = fields

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

    def get_memberships(self, obj):
        memberships = getattr(obj, "active_memberships_for_me", None)
        if memberships is None:
            memberships = (
                obj.club_memberships.filter(is_active=True)
                .select_related("club", "court")
                .order_by("club__name", "role", "id")
            )
        return UserMembershipSerializer(memberships, many=True).data


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
            "is_platform_admin",
            "created_by",
        )
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
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
            "is_platform_admin",
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
        return User.objects.create_user(
            password=password,
            is_staff=False,
            is_superuser=False,
            **validated_data,
        )

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
            "is_platform_admin",
        )

    def to_representation(self, instance):
        return UserListSerializer(instance, context=self.context).data
