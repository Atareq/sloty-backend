"""Settlement authorization on top of Profile-based Spine scope."""

from typing import Collection, Dict, Optional, Set

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.settlements.models import Settlement


def get_active_user_roles(club, user: User) -> Set[str]:
    profile = getattr(user, "profile", None)
    if profile is None or not profile.is_enabled:
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
        and staff_profile.club_id == club.pk
    ):
        return {Role.STAFF}
    return set()


def get_active_user_roles_by_id(club, user_ids: Collection[int]) -> Dict[int, Set[str]]:
    users = (
        User.objects.filter(pk__in=user_ids)
        .select_related("profile__owner_profile", "profile__staff_profile__club")
        .prefetch_related("profile__owner_profile__clubs")
    )
    by_id = {user.id: get_active_user_roles(club, user) for user in users}
    return {user_id: by_id.get(user_id, set()) for user_id in user_ids}


def validate_user_profile(*, context: RequestAccessContext, user: User) -> None:
    if context.role == Role.ADMIN and user.id == context.user.id:
        return
    if not get_active_user_roles(context.club, user):
        raise serializers.ValidationError(
            {"user": _("User has no active profile scope in this club.")}
        )


def can_manage_settlements(context: RequestAccessContext) -> bool:
    return context.role in {Role.ADMIN, Role.OWNER}


def apply_collector_scope(context: RequestAccessContext, queryset):
    return (
        queryset
        if can_manage_settlements(context)
        else queryset.filter(lines__transaction__created_by=context.user).distinct()
    )


def can_access_settlement(
    context: RequestAccessContext, settlement: Settlement
) -> bool:
    return bool(
        settlement
        and settlement.club_id == context.club.id
        and (
            can_manage_settlements(context)
            or settlement.lines.filter(transaction__created_by=context.user).exists()
        )
    )


def can_approve_user(
    context: RequestAccessContext, user_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context)


def can_view_user_in_summary(
    context: RequestAccessContext, user_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context) or user_id == context.user.id


def validate_preview_authority(
    *, context: RequestAccessContext, user: Optional[User] = None
) -> None:
    if user is not None:
        validate_user_profile(context=context, user=user)
        if can_manage_settlements(context) or user.id == context.user.id:
            return
        raise PermissionDenied(_("You cannot preview settlements for this user."))
    elif not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot preview settlements for this club."))


def validate_settlement_authority(
    *, context: RequestAccessContext, user: Optional[User] = None
) -> None:
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if user is not None:
        validate_user_profile(context=context, user=user)


def validate_unsettled_summary_authority(
    *, context: RequestAccessContext, user: Optional[User] = None
) -> None:
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if user is not None:
        validate_user_profile(context=context, user=user)


def build_unsettled_summary(
    *, context: RequestAccessContext, user: Optional[User] = None
) -> dict:
    from apps.settlements.serializers import SettlementUnsettledSummaryRequestSerializer

    serializer = SettlementUnsettledSummaryRequestSerializer(
        data={"user": user.id if user else None},
        context={"access_context": context},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.get_summary()
