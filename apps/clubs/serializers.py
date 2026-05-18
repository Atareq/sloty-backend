from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.clubs.permissions import is_active_club_owner


class ClubListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "city",
            "area",
            "phone_number",
            "is_active",
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created", "modified")


class ClubDetailSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "city",
            "area",
            "address",
            "phone_number",
            "notes",
            "is_active",
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created_by", "created", "modified")


class ClubCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "id",
            "name",
            "city",
            "area",
            "address",
            "phone_number",
            "notes",
            "is_active",
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
        )
        read_only_fields = ("id",)

    def to_representation(self, instance):
        return ClubDetailSerializer(instance, context=self.context).data


class ClubUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Club
        fields = (
            "name",
            "city",
            "area",
            "address",
            "phone_number",
            "notes",
            "is_active",
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
        )

    def to_representation(self, instance):
        return ClubDetailSerializer(instance, context=self.context).data


class ClubMembershipSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ClubMembership
        fields = (
            "id",
            "club",
            "user",
            "role",
            "is_active",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created_by", "created", "modified")

    def validate(self, attrs):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)
        club = attrs.get("club", getattr(self.instance, "club", None))
        user = attrs.get("user", getattr(self.instance, "user", None))
        role = attrs.get("role", getattr(self.instance, "role", None))
        is_active = attrs.get(
            "is_active",
            getattr(self.instance, "is_active", True),
        )

        if self.instance is not None:
            for field_name in ("club", "user", "role"):
                if field_name in attrs and attrs[field_name] != getattr(
                    self.instance, field_name
                ):
                    raise serializers.ValidationError(
                        {field_name: "Existing memberships cannot change scope."}
                    )

        if role == ClubMembership.Role.OWNER and user.role != User.Role.CLUB_OWNER:
            raise serializers.ValidationError(
                {"user": "OWNER assignments require a CLUB_OWNER user."}
            )
        if role == ClubMembership.Role.MANAGER and user.role != User.Role.MANAGER:
            raise serializers.ValidationError(
                {"user": "MANAGER assignments require a MANAGER user."}
            )

        if not request_user.is_platform_super_admin():
            if not is_active_club_owner(request_user, club):
                raise PermissionDenied("You cannot manage memberships for this club.")
            if role != ClubMembership.Role.MANAGER:
                raise PermissionDenied(
                    "Club owners can only manage manager assignments."
                )

        if is_active:
            duplicate_membership = ClubMembership.objects.filter(
                club=club,
                user=user,
                role=role,
                is_active=True,
            )
            if self.instance is not None:
                duplicate_membership = duplicate_membership.exclude(pk=self.instance.pk)
            if duplicate_membership.exists():
                raise serializers.ValidationError(
                    "This active club membership already exists."
                )

            if role == ClubMembership.Role.MANAGER:
                active_manager_membership = ClubMembership.objects.filter(
                    user=user,
                    role=ClubMembership.Role.MANAGER,
                    is_active=True,
                )
                if self.instance is not None:
                    active_manager_membership = active_manager_membership.exclude(
                        pk=self.instance.pk
                    )
                if active_manager_membership.exists():
                    raise serializers.ValidationError(
                        {"user": "A manager can have only one active club assignment."}
                    )

        return attrs
