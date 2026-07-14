from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.clubs.models import Club, ClubMembership, generate_unique_club_slug
from apps.common.egypt_locations import (
    get_all_city_choices,
    get_governorate_choices,
    is_valid_city_for_governorate,
)


class ClubLocationValidationMixin:
    governorate = serializers.ChoiceField(
        choices=get_governorate_choices(),
        error_messages={"invalid_choice": "Invalid governorate choice."},
    )
    city = serializers.ChoiceField(
        choices=get_all_city_choices(),
        error_messages={"invalid_choice": "Invalid city choice."},
    )

    def validate_location(self, attrs):
        instance = getattr(self, "instance", None)
        governorate = attrs.get(
            "governorate",
            getattr(instance, "governorate", None),
        )
        city = attrs.get("city", getattr(instance, "city", None))
        if (
            governorate
            and city
            and not is_valid_city_for_governorate(
                governorate,
                city,
            )
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
            "slug",
            "governorate",
            "city",
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
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
        )
        read_only_fields = ("id",)
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
        }

    def validate_slug(self, value):
        if value and Club.objects.filter(slug=value).exists():
            raise serializers.ValidationError("A club with this slug already exists.")
        return value

    def validate(self, attrs):
        attrs = self.validate_location(attrs)
        if not attrs.get("slug"):
            attrs["slug"] = generate_unique_club_slug(attrs["name"])
        return attrs

    def to_representation(self, instance):
        return ClubDetailSerializer(instance, context=self.context).data


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
            "manager_can_settle_transactions",
            "manager_can_change_pricing",
        )

    def validate(self, attrs):
        return self.validate_location(attrs)

    def to_representation(self, instance):
        return ClubDetailSerializer(instance, context=self.context).data


class ClubMembershipSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    club = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ClubMembership
        fields = (
            "id",
            "club",
            "user",
            "role",
            "court",
            "is_active",
            "created_by",
            "created",
            "modified",
        )
        read_only_fields = ("id", "created_by", "created", "modified")

    def validate(self, attrs):
        access = self.context["club_access"]
        club = access.club
        user = attrs.get("user", getattr(self.instance, "user", None))
        role = attrs.get("role", getattr(self.instance, "role", None))
        court = attrs.get("court", getattr(self.instance, "court", None))
        is_active = attrs.get(
            "is_active",
            getattr(self.instance, "is_active", True),
        )

        if self.instance is not None:
            for field_name in ("user", "role", "court"):
                if field_name in attrs and attrs[field_name] != getattr(
                    self.instance, field_name
                ):
                    raise serializers.ValidationError(
                        {field_name: "Existing memberships cannot change scope."}
                    )

        if role in {ClubMembership.Role.OWNER, ClubMembership.Role.MANAGER} and court:
            raise serializers.ValidationError(
                {"court": "OWNER and MANAGER memberships cannot be court-scoped."}
            )
        if role == ClubMembership.Role.STAFF and not court:
            raise serializers.ValidationError(
                {"court": "STAFF memberships require a court."}
            )
        if court and court.club_id != club.id:
            raise serializers.ValidationError(
                {"court": "Membership court must belong to the selected club."}
            )

        if self.instance is None:
            if not access.can_create_membership(role, court=court):
                raise PermissionDenied("You cannot create this membership.")
        else:
            if not access.can_manage_memberships():
                raise PermissionDenied("You cannot manage memberships for this club.")
            if (
                not access.is_platform_admin
                and self.instance.role == self.Meta.model.Role.OWNER
            ):
                raise PermissionDenied("Club owners cannot manage owner memberships.")

        if is_active:
            duplicate_membership = ClubMembership.objects.filter(
                club=club,
                user=user,
                role=role,
                is_active=True,
            )
            if role == ClubMembership.Role.STAFF:
                duplicate_membership = duplicate_membership.filter(court=court)
            else:
                duplicate_membership = duplicate_membership.filter(court__isnull=True)
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
            if role == ClubMembership.Role.STAFF:
                active_staff_membership = ClubMembership.objects.filter(
                    user=user,
                    role=ClubMembership.Role.STAFF,
                    is_active=True,
                )
                if self.instance is not None:
                    active_staff_membership = active_staff_membership.exclude(
                        pk=self.instance.pk
                    )
                if active_staff_membership.exists():
                    raise serializers.ValidationError(
                        {
                            "user": "A staff user can have only "
                            "one active court assignment."
                        }
                    )

        return attrs
