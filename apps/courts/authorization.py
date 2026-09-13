"""
Court domain authorization invariant.

ARCHITECTURAL NOTE:
Court list/retrieve/create/update authorization is handled entirely by the
centralized Authorization Spine:
  - SlotyScopedResourceMixin + Court/CourtWorkingHour.authorization_config
    resolve the authorized queryset (club boundary, then court boundary for
    Staff assigned-court isolation).
  - SlotyBasePermission + ROLE_PERMISSIONS (apps/common/authorization/matrix.py)
    gate every Role -> ViewSet -> Action combination, including the
    Admin/Owner-only Court create & field-mutation restriction.

This module exists ONLY because one Courts business rule cannot be expressed
by the static Role -> ViewSet -> Action matrix: a MANAGER's authority to
replace weekly working hours/pricing depends on a per-membership delegation
flag (`ClubMembership.manager_can_change_pricing`), not on role alone. The
matrix has no concept of per-row membership flags, so this single invariant
is evaluated here rather than by extending the shared spine for one
domain-specific delegation flag.

Do not add general court-scope or queryset logic to this module; that
belongs to the shared Authorization Spine (apps/common/authorization/).
"""

from apps.common.authorization.roles import Role


def can_manage_working_hours(context, court) -> bool:
    """
    Determine whether the context actor may replace weekly working hours
    and pricing periods for the given court.

    Court accessibility itself (club boundary + Staff assigned-court
    isolation) is already enforced upstream by the scoped queryset that
    resolved `court`. This function evaluates only the write-authority
    delegation on top of that:
    - Platform Admin and Owner may always manage working hours.
    - Manager may manage working hours only when their membership has
      `manager_can_change_pricing=True`.
    - Staff may never manage working hours (also denied by the matrix).
    """
    if context.is_platform_admin or context.role == Role.OWNER:
        return True
    if context.role == Role.MANAGER:
        return bool(
            context.membership and context.membership.manager_can_change_pricing
        )
    return False
