"""
Settlement domain authorization the Spine cannot express.

ARCHITECTURAL INVARIANTS:
1. Ordinary Club WHERE belongs to Authorization Spine v2
   (Settlement.authorization_config, default_scope="club").
2. Collector visibility is role- and flag-dependent, so it is not a Spine
   ResourceScope. apply_collector_scope() narrows an already club-scoped
   queryset: actors without can_manage_settlements() see only
   collected_by=request.user.
3. Financial custody is Club + optional Collector. Court is NEVER part of
   financial custody and must not be applied as a Spine court scope.
4. Self-approval bans, manager_can_settle_transactions, and manager-cannot-
   settle-Owner remain domain business rules evaluated before pure financial
   services.
5. Financial functions (get_unsettled_transactions_queryset, build_custody,
   settle_custody, mark_settlement_settled) receive already-authorized
   entities and do not inspect roles, permissions, court assignments, or
   RequestAccessContext.
"""

from typing import Collection, Dict, Optional, Set

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.clubs.models import ClubMembership
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.settlements.models import Settlement

SELF_APPROVAL_MESSAGE = _("You cannot approve your own settlement.")


def get_active_collector_roles(club, user: User) -> Set[str]:
    """Return active membership roles for a user in a club."""
    return set(
        ClubMembership.objects.granting_access()
        .filter(club=club, user=user)
        .values_list("role", flat=True)
    )


def get_active_collector_roles_by_id(
    club, user_ids: Collection[int]
) -> Dict[int, Set[str]]:
    """Return active membership roles grouped by user_id for a club."""
    roles_by_user = {user_id: set() for user_id in user_ids}
    if not user_ids:
        return roles_by_user
    for row in (
        ClubMembership.objects.granting_access()
        .filter(club=club, user_id__in=user_ids)
        .values("user_id", "role")
    ):
        roles_by_user[row["user_id"]].add(row["role"])
    return roles_by_user


def validate_collected_by_membership(
    *, context: RequestAccessContext, collector: User
) -> None:
    """Ensure collector has an active access-granting membership in the club."""
    if context.is_platform_admin and collector.id == context.user.id:
        return
    active_roles = get_active_collector_roles(context.club, collector)
    if not active_roles:
        raise serializers.ValidationError(
            {"collected_by": _("User must have an active membership in this club.")}
        )


def can_manage_settlements(context: RequestAccessContext) -> bool:
    """Determine whether context holds settlement management authority."""
    if context.is_platform_admin or context.role == Role.OWNER:
        return True
    if context.role == Role.MANAGER:
        return bool(
            getattr(context.membership, "manager_can_settle_transactions", False)
        )
    return False


def apply_collector_scope(context: RequestAccessContext, queryset):
    """
    Narrow an already club-scoped Settlement queryset to the actor's custody.

    Platform Admin, Owner, and Managers with manager_can_settle_transactions
    see every collector in the club. Staff and Managers without the flag see
    only rows they collected. This is not court assignment.
    """
    if not can_manage_settlements(context):
        return queryset.filter(collected_by=context.user)
    return queryset


def can_access_settlement(
    context: RequestAccessContext, settlement: Settlement
) -> bool:
    """Evaluate object-level permission for a Settlement instance."""
    if settlement is None or settlement.club_id != context.club.id:
        return False
    if can_manage_settlements(context):
        return True
    return settlement.collected_by_id == context.user.id


def can_approve_collector(
    context: RequestAccessContext,
    collector_id: int,
    roles: Optional[Set[str]] = None,
) -> bool:
    """Determine whether the context actor can approve settlements for a collector."""
    if not can_manage_settlements(context):
        return False
    if context.is_platform_admin or context.role == Role.OWNER:
        return True
    if context.role == Role.MANAGER:
        if collector_id == context.user.id:
            return False
        if roles is not None and Role.OWNER in roles:
            return False
        return True
    return False


def can_view_collector_in_summary(
    context: RequestAccessContext,
    collector_id: int,
    roles: Optional[Set[str]] = None,
) -> bool:
    """
    Determine whether context actor is permitted to see this collector in summary.

    Rules:
    - Platform Admin and Owner can view all active collectors.
    - Managers cannot view Owner collectors in the unsettled summary.
    - Staff cannot view anyone other than themselves.
    """
    if context.is_platform_admin or context.role == Role.OWNER:
        return True
    if context.role == Role.MANAGER:
        if roles is not None and Role.OWNER in roles:
            return False
        return True
    return collector_id == context.user.id


def validate_preview_authority(
    *, context: RequestAccessContext, collector: User
) -> None:
    """
    Validate that caller is authorized to preview custody for `collector`.

    Rules:
    - Staff may ONLY preview themselves (collector.id == context.user.id).
    - Manager may preview self. Previewing other collectors requires
      manager_can_settle_transactions=True, and target cannot have OWNER role.
    - Owner and Platform Admin may preview any active collector.
    - Collector must have active membership in club (except platform admin self).
    """
    validate_collected_by_membership(context=context, collector=collector)

    if context.is_platform_admin or context.role == Role.OWNER:
        return

    if context.role == Role.STAFF:
        if collector.id != context.user.id:
            raise PermissionDenied(_("You cannot preview settlements for this user."))
        return

    if context.role == Role.MANAGER:
        if collector.id == context.user.id:
            return
        if not getattr(context.membership, "manager_can_settle_transactions", False):
            raise PermissionDenied(_("You cannot preview settlements for this user."))
        target_roles = get_active_collector_roles(context.club, collector)
        if Role.OWNER in target_roles:
            raise PermissionDenied(_("You cannot preview settlements for this user."))
        return

    raise PermissionDenied(_("You cannot preview settlements for this user."))


def validate_settlement_authority(
    *, context: RequestAccessContext, collector: User
) -> None:
    """
    Validate that caller is authorized to settle custody for `collector`.

    Rules:
    - Staff cannot settle.
    - Manager requires manager_can_settle_transactions=True, cannot settle self,
      and cannot settle user with OWNER role.
    - Owner and Platform Admin can settle any active collector, and can settle self.
    - Collector must have active membership in club (except platform admin self).
    """
    validate_collected_by_membership(context=context, collector=collector)

    if context.role == Role.STAFF:
        raise PermissionDenied(_("You cannot manage settlements for this club."))

    if context.role == Role.MANAGER:
        if not getattr(context.membership, "manager_can_settle_transactions", False):
            raise PermissionDenied(_("You cannot manage settlements for this club."))
        if collector.id == context.user.id:
            raise SlotyAPIException(
                status_code=status.HTTP_403_FORBIDDEN,
                code="SELF_SETTLEMENT_APPROVAL_FORBIDDEN",
                message=SELF_APPROVAL_MESSAGE,
            )
        target_roles = get_active_collector_roles(context.club, collector)
        if Role.OWNER in target_roles:
            raise PermissionDenied(_("You cannot approve settlements for this user."))
        return

    if context.role == Role.OWNER or context.is_platform_admin:
        return

    raise PermissionDenied(_("You cannot manage settlements for this club."))


def validate_unsettled_summary_authority(
    *,
    context: RequestAccessContext,
    collector: Optional[User] = None,
) -> None:
    """
    Validate authority to access unsettled transaction summary.

    Rules:
    - Staff: Denied.
    - Manager without manager_can_settle_transactions: Denied.
    - Owner, Admin, Manager with flag: Allowed.
    - If specific collector requested:
      - Must have active membership in club (400).
      - If caller is Manager: target cannot have OWNER role (403).
    """
    if context.role == Role.STAFF:
        raise PermissionDenied(_("You cannot manage settlements for this club."))

    if context.role == Role.MANAGER:
        if not getattr(context.membership, "manager_can_settle_transactions", False):
            raise PermissionDenied(_("You cannot manage settlements for this club."))
        if collector is not None:
            target_roles = get_active_collector_roles(context.club, collector)
            if not target_roles:
                raise serializers.ValidationError(
                    {
                        "collected_by": _(
                            "User must have an active membership in this club."
                        )
                    }
                )
            if Role.OWNER in target_roles:
                raise PermissionDenied(
                    _("You cannot preview settlements for this user.")
                )
        return

    if collector is not None:
        if not (context.is_platform_admin and collector.id == context.user.id):
            target_roles = get_active_collector_roles(context.club, collector)
            if not target_roles:
                raise serializers.ValidationError(
                    {
                        "collected_by": _(
                            "User must have an active membership in this club."
                        )
                    }
                )


def build_unsettled_summary(
    *,
    context: RequestAccessContext,
    collector: Optional[User] = None,
) -> dict:
    """
    Legacy compatibility helper for unsettled summary assembly.

    Delegates to the application serializer layer. Calculation is owned by
    the financial layer (services.py); authorization is owned by this module.
    """
    from apps.settlements.serializers import SettlementUnsettledSummaryRequestSerializer

    serializer = SettlementUnsettledSummaryRequestSerializer(
        data={"collected_by": collector.id if collector else None},
        context={"access_context": context},
    )
    serializer.is_valid(raise_exception=True)
    return serializer.get_summary()
