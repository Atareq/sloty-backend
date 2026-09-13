"""
SlotyBasePermission.

Generic DRF BasePermission evaluating the centralized Role -> ViewSet -> Action matrix.
Consumes RequestAccessContext facts and does not contain domain business logic.
"""

from rest_framework.permissions import BasePermission

from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope

METHOD_ACTION_MAP = {
    "GET": "list",
    "POST": "create",
    "PUT": "update",
    "PATCH": "partial_update",
    "DELETE": "destroy",
    "HEAD": "list",
    "OPTIONS": "list",
}


class SlotyBasePermission(BasePermission):
    """
    Centralized role-based DRF permission class.

    Responsibilities:
    1. Read RequestAccessContext (pre-resolved by view mixin or fallback).
    2. Identify current role, ViewSet, and DRF action.
    3. Evaluate against centralized ROLE_PERMISSIONS matrix.
    4. Provide extension hook for object-level permissions.
    """

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False

        # Read context already resolved on the request
        context = getattr(request, "access_context", None)
        if context is None:
            # Fallback resolution if view mixin was omitted
            club_slug = view.kwargs.get(
                getattr(view, "club_lookup_url_kwarg", "club_slug")
            )
            court_id = view.kwargs.get(
                getattr(view, "court_lookup_url_kwarg", "court_id")
            )
            if club_slug:
                context = resolve_club_scope(
                    request, club_slug=club_slug, court_id=court_id
                )
            else:
                context = resolve_global_scope(request)

        # Identify DRF action
        action = getattr(view, "action", None)
        if not action:
            action = METHOD_ACTION_MAP.get(request.method, request.method.lower())

        # Identify ViewSet name
        view_key = getattr(view, "permission_view_name", view.__class__.__name__)

        return is_action_allowed(context.role, view_key, action)

    def has_object_permission(self, request, view, obj) -> bool:
        """
        Extension hook for object-level permissions.

        If the ViewSet implements `check_object_permission(request, obj)`,
        delegates to it.
        Otherwise returns True by default, allowing secondary object permission classes
        to enforce domain-specific object rules.
        """
        if hasattr(view, "check_object_permission"):
            return view.check_object_permission(request, obj)
        return True
