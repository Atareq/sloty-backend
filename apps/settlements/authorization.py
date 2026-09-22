"""Settlement authorization on top of Profile-based Spine scope."""

from typing import Collection, Dict, Optional, Set

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.profiles.models import Profile
from apps.settlements.models import Settlement


def get_active_profile_roles(club, profile: Profile) -> Set[str]:
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


def get_active_profile_roles_by_id(
    club, profile_ids: Collection[int]
) -> Dict[int, Set[str]]:
    profiles = (
        Profile.objects.filter(pk__in=profile_ids)
        .select_related("owner_profile", "staff_profile__club")
        .prefetch_related("owner_profile__clubs")
    )
    by_id = {
        profile.id: get_active_profile_roles(club, profile) for profile in profiles
    }
    return {p_id: by_id.get(p_id, set()) for p_id in profile_ids}


def validate_collector_profile(
    *, context: RequestAccessContext, profile: Profile
) -> None:
    if (
        context.role == Role.ADMIN
        and context.profile
        and profile.id == context.profile.id
    ):
        return
    if not get_active_profile_roles(context.club, profile):
        raise serializers.ValidationError(
            {"collected_by": _("Profile has no active profile scope in this club.")}
        )


def can_manage_settlements(context: RequestAccessContext) -> bool:
    return context.role in {Role.ADMIN, Role.OWNER}


def apply_collector_scope(context: RequestAccessContext, queryset):
    if can_manage_settlements(context):
        return queryset
    return queryset.filter(lines__transaction__created_by=context.profile).distinct()


def can_access_settlement(
    context: RequestAccessContext, settlement: Settlement
) -> bool:
    has_collector_line = settlement.lines.filter(
        transaction__created_by=context.profile
    ).exists()
    return bool(
        settlement
        and settlement.club_id == context.club.id
        and (can_manage_settlements(context) or has_collector_line)
    )


def can_approve_collector(
    context: RequestAccessContext, collector_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context)


def can_view_collector_in_summary(
    context: RequestAccessContext, collector_id: int, roles: Optional[Set[str]] = None
) -> bool:
    return can_manage_settlements(context) or bool(
        context.profile and collector_id == context.profile.id
    )


def validate_preview_authority(
    *, context: RequestAccessContext, collector: Optional[Profile] = None
) -> None:
    if collector is not None:
        validate_collector_profile(context=context, profile=collector)
        if can_manage_settlements(context) or (
            context.profile and collector.id == context.profile.id
        ):
            return
        raise PermissionDenied(_("You cannot preview settlements for this collector."))
    elif not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot preview settlements for this club."))


def validate_settlement_authority(
    *, context: RequestAccessContext, collector: Optional[Profile] = None
) -> None:
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if collector is not None:
        validate_collector_profile(context=context, profile=collector)


def validate_unsettled_summary_authority(
    *, context: RequestAccessContext, collector: Optional[Profile] = None
) -> None:
    if not can_manage_settlements(context):
        raise PermissionDenied(_("You cannot manage settlements for this club."))
    if collector is not None:
        validate_collector_profile(context=context, profile=collector)


def build_unsettled_summary(
    *, context: RequestAccessContext, collector: Optional[Profile] = None
) -> dict:
    from apps.settlements.serializers import SettlementUnsettledSummaryRequestSerializer

    serializer = SettlementUnsettledSummaryRequestSerializer(
        data={"collected_by": collector.id if collector else None},
        context={"access_context": context},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.get_summary()
