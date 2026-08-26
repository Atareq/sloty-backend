from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.clubs.models import ClubMembership
from apps.common.exceptions import SlotyAPIException

MEMBERSHIP_ALREADY_DELETED_MESSAGE = _("This club membership has already been removed.")
MEMBERSHIP_DELETED_CANNOT_REACTIVATE_MESSAGE = _(
    "A permanently removed membership cannot be reactivated."
)


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


def soft_delete_membership(*, access, membership, actor):
    if membership.club_id != access.club.id:
        raise PermissionDenied("You cannot manage memberships for this club.")
    if not access.can_manage_memberships():
        raise PermissionDenied("You cannot manage memberships for this club.")
    if not access.is_platform_admin and membership.role == ClubMembership.Role.OWNER:
        raise PermissionDenied("Club owners cannot manage owner memberships.")
    if membership.deleted_at is not None:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="MEMBERSHIP_ALREADY_DELETED",
            message=MEMBERSHIP_ALREADY_DELETED_MESSAGE,
        )

    with transaction.atomic():
        before_data = {
            "membership_id": membership.id,
            "user_id": membership.user_id,
            "role": membership.role,
            "is_active": membership.is_active,
            "deleted_at": None,
        }
        membership.is_active = False
        membership.deleted_at = timezone.now()
        membership.deleted_by = actor
        membership.save(
            update_fields=["is_active", "deleted_at", "deleted_by", "modified"]
        )
        record_audit_log(
            club=membership.club,
            court=membership.court,
            actor=actor,
            action=AuditLog.Action.MEMBERSHIP_DELETED,
            entity_type="ClubMembership",
            entity_id=membership.id,
            before_data=before_data,
            after_data={
                "membership_id": membership.id,
                "user_id": membership.user_id,
                "role": membership.role,
                "is_active": membership.is_active,
                "deleted_at": membership.deleted_at.isoformat(),
                "deleted_by": actor.id if actor else None,
            },
        )
    return membership
