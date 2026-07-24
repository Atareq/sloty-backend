from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.clubs.models import ClubMembership


def create_club_member(
    *,
    access,
    role,
    created_by,
    court=None,
    manager_can_settle_transactions=False,
    manager_can_change_pricing=False,
    user_data=None,
    user=None,
):
    if (user_data is None) == (user is None):
        raise serializers.ValidationError(
            {"user": "Provide exactly one of user or user_id."}
        )
    if not access.can_create_membership(role, court=court):
        raise PermissionDenied("You cannot create this membership.")
    if role in {ClubMembership.Role.OWNER, ClubMembership.Role.MANAGER} and court:
        raise serializers.ValidationError(
            {"court": "OWNER and MANAGER memberships cannot be court-scoped."}
        )
    if role == ClubMembership.Role.STAFF and not court:
        raise serializers.ValidationError(
            {"court": "STAFF memberships require a court."}
        )
    if role != ClubMembership.Role.MANAGER and (
        manager_can_settle_transactions or manager_can_change_pricing
    ):
        raise serializers.ValidationError(
            {
                "manager_permissions": (
                    "Manager permission flags are only valid for MANAGER memberships."
                )
            }
        )
    if court and court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Membership court must belong to the selected club."}
        )

    with transaction.atomic():
        if user_data is not None:
            password = user_data.pop("password")
            user = User.objects.create_user(
                password=password,
                is_active=True,
                is_platform_admin=False,
                is_staff=False,
                is_superuser=False,
                created_by=created_by,
                **user_data,
            )

        membership = ClubMembership.objects.create(
            club=access.club,
            user=user,
            role=role,
            court=court,
            manager_can_settle_transactions=manager_can_settle_transactions,
            manager_can_change_pricing=manager_can_change_pricing,
            is_active=True,
            created_by=created_by,
        )
    return membership
