from rest_framework import serializers

from apps.accounts.models import User
from apps.clubs.models import ClubMembership


class UserMembershipClubSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.SlugField()
    name = serializers.CharField()


class UserMembershipCourtSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserMembershipSerializer(serializers.ModelSerializer):
    club = UserMembershipClubSerializer(read_only=True)
    court = UserMembershipCourtSerializer(read_only=True)

    class Meta:
        model = ClubMembership
        fields = (
            "id",
            "role",
            "club",
            "court",
        )


class UserMeSerializer(serializers.ModelSerializer):
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
            "memberships",
        )
        read_only_fields = fields

    def get_memberships(self, obj):
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

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)

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
