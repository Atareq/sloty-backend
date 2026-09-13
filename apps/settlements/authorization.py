"""
Settlement domain authorization and scope resolution.

ARCHITECTURAL INVARIANTS:
1. Authorization determines whether a caller (represented by RequestAccessContext)
   is allowed to access or mutate a custody scope (Club + Collector).
2. Authorization happens BEFORE financial processing. Financial functions
   (get_unsettled_transactions_queryset, build_custody, settle_custody) receive
   already-authorized domain entities and do not inspect roles, permissions, or context.
3. Financial custody is strictly scoped by Club + optional Collector.
   Court is NEVER part of financial custody.
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
from apps.settlements.services import (
    build_current_custody_collector_rows,
    get_unsettled_transactions_queryset,
)

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
    Build read-only unsettled transaction summary for view action.

    Derives candidates from get_unsettled_transactions_queryset(
        club=context.club, collector=collector
    ), aggregates collector rows, filters out owners for managers, and
    decorates rows with is_self and can_approve.
    """
    validate_unsettled_summary_authority(context=context, collector=collector)
    queryset = get_unsettled_transactions_queryset(
        club=context.club,
        collector=collector,
    )
    grouped_rows = build_current_custody_collector_rows(queryset)
    collector_ids = [row["collected_by"] for row in grouped_rows]
    roles_by_user_id = get_active_collector_roles_by_id(context.club, collector_ids)
    results = []
    for row in grouped_rows:
        collector_id = row["collected_by"]
        roles = roles_by_user_id.get(collector_id, set())
        if context.role == Role.MANAGER and Role.OWNER in roles:
            continue
        results.append(
            row
            | {
                "is_self": bool(context.user and collector_id == context.user.id),
                "can_approve": can_approve_collector(context, collector_id, roles),
            }
        )
    results.sort(
        key=lambda item: (item["collected_by_name"].casefold(), item["collected_by"])
    )
    return {"results": results}
