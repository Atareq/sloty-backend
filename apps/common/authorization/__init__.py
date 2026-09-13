"""
Sloty Authorization Engine Foundation.

Provides:
- RequestAccessContext: Pure fact container.
- resolve_club_scope, resolve_global_scope: Scope resolution.
- Role: Operational role definitions.
- ROLE_PERMISSIONS, is_action_allowed: Centralized role-to-action matrix.
- SlotyBasePermission: Base DRF permission consuming context and matrix.
- ClubScopedViewMixin: DRF ViewSet mixin attaching context before permission check.
"""

from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.matrix import ROLE_PERMISSIONS, is_action_allowed
from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope
from apps.common.authorization.roles import Role

__all__ = [
    "RequestAccessContext",
    "resolve_club_scope",
    "resolve_global_scope",
    "Role",
    "ROLE_PERMISSIONS",
    "is_action_allowed",
    "SlotyBasePermission",
    "ClubScopedViewMixin",
]
