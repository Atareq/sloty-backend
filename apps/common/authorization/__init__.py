"""
Sloty Authorization Engine Foundation.

Provides:
- RequestAccessContext: Pure fact container.
- resolve_club_scope, resolve_global_scope: Scope resolution.
- Role: Operational role definitions.
- ROLE_PERMISSIONS, is_action_allowed: Centralized role-to-action matrix.
- SlotyBasePermission: Base DRF permission consuming context and matrix.
- ClubScopedViewMixin: DRF ViewSet mixin attaching context before permission check.
- ResourceScope: Stable model scope keys.
- scoped_queryset: Generic fail-closed queryset resolver.
- SlotyScopedResourceMixin: Model-driven DRF queryset integration.
"""

from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.contracts import (
    SELF_SCOPE_PATH,
    ModelAuthorizationConfig,
    ResourceScopeDefinition,
    load_authorization_config,
)
from apps.common.authorization.matrix import ROLE_PERMISSIONS, is_action_allowed
from apps.common.authorization.mixins import (
    ClubScopedViewMixin,
    SlotyScopedResourceMixin,
)
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope

__all__ = [
    "RequestAccessContext",
    "resolve_club_scope",
    "resolve_global_scope",
    "Role",
    "ROLE_PERMISSIONS",
    "is_action_allowed",
    "SlotyBasePermission",
    "ClubScopedViewMixin",
    "ResourceScope",
    "ResourceScopeDefinition",
    "SELF_SCOPE_PATH",
    "ModelAuthorizationConfig",
    "load_authorization_config",
    "scoped_queryset",
    "SlotyScopedResourceMixin",
]
