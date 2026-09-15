"""Settlement-specific authorization on top of Profile-based Spine scope."""

from typing import Collection, Dict, Optional, Set

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.profiles.models import Profile
from apps.settlements.models import Settlement

SELF_APPROVAL_MESSAGE = _("You cannot approve your own settlement.")


def get_active_collector_roles(club, user: User) -> Set[str]:
    profile = Profile.objects.filter(user=user).first()
    if profile is None:
        return set()
    if profile.role == Role.ADMIN:
        return {Role.ADMIN}
    owner_profile = getattr(profile, "owner_profile", None)
    if (
        profile.role == Role.OWNER
        and owner_profile
        and owner_profile.clubs.filter(pk=club.pk).exists()
    ):
        return {Role.OWNER}
    staff_profile = getattr(profile, "staff_profile", None)
    if (
        profile.role == Role.STAFF
        and staff_profile
        and staff_profile.court.club_id == club.pk
    ):
        return {Role.STAFF}
    return set()


def get_active_collector_roles_by_id(
    club, user_ids: Collection[int]
) -> Dict[int, Set[str]]:
    return {
        user_id: get_active_collector_roles(club, User.objects.get(pk=user_id))
        for user_id in user_ids
    }


def validate_collected_by_membership(
    *, context: RequestAccessContext, collector: User
) -> None:
    if context.is_platform_admin and collector.id == context.user.id:
        return
    if not get_active_collector_roles(context.club, collector):
        raise serializers.ValidationError(
            {"collected_by": _("User has no active profile scope in this club.")}
        )


def can_manage_settlements(context: RequestAccessContext) -> bool:
    return context.is_platform_admin or context.role == Role.OWNER


def apply_collector_scope(context: RequestAccessContext, queryset):
    return (
        queryset
        if can_manage_settlements(context)
        else queryset.filter(collected_by=context.user)
    )


def can_access_settlement(
    context: RequestAccessContext, settlement: Settlement
) -> bool:
    return bool(
        settlement
        and settlement.club_id == context.club.id
        and (
            can_manage_settlements(context)
            or settlement.collected_by_id == context.user.id
        )
    )


def can_approve_collector(
    context: RequestAccessContext, collector_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context)


def can_view_collector_in_summary(
    context: RequestAccessContext, collector_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context) or collector_id == context.user.id


def validate_preview_authority(
    *, context: RequestAccessContext, collector: User
) -> None:
    validate_collected_by_membership(context=context, collector=collector)
    if can_manage_settlements(context) or collector.id == context.user.id:
        return
    raise PermissionDenied(_("You cannot preview settlements for this user."))


def validate_settlement_authority(
    *, context: RequestAccessContext, collector: User
) -> None:
    validate_collected_by_membership(context=context, collector=collector)
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if collector.id == context.user.id and not context.is_platform_admin:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SELF_SETTLEMENT_APPROVAL_FORBIDDEN",
            message=SELF_APPROVAL_MESSAGE,
        )


def validate_unsettled_summary_authority(
    *, context: RequestAccessContext, collector: Optional[User] = None
) -> None:
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if collector is not None:
        validate_collected_by_membership(context=context, collector=collector)


def build_unsettled_summary(
    *, context: RequestAccessContext, collector: Optional[User] = None
) -> dict:
    from apps.settlements.serializers import SettlementUnsettledSummaryRequestSerializer

    serializer = SettlementUnsettledSummaryRequestSerializer(
        data={"collected_by": collector.id if collector else None},
        context={"access_context": context},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.get_summary()
