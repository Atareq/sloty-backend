"""
Centralized Scope Resolver.

Resolves request.user -> URL club -> validated RequestAccessContext.
The URL club scope is authoritative.
"""

from typing import Any, Optional

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated

from apps.clubs.models import Club, ClubMembership
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException

CLUB_ACCESS_REVOKED_MESSAGE = _("Your access to the selected club is no longer active.")

ROLE_PRECEDENCE = {
    Role.OWNER: 0,
    Role.MANAGER: 1,
    Role.STAFF: 2,
}


def resolve_club_scope(
    request,
    club_slug: str,
    court_id: Optional[Any] = None,
) -> RequestAccessContext:
    """
    Resolve and validate club-scoped access context for /api/v1/clubs/{club_slug}/...

    Invariants:
    1. The URL club scope is authoritative. Client payloads cannot override it.
    2. Revoked or missing access raises standard CLUB_ACCESS_REVOKED (HTTP 403).
    3. Caches context on request._access_context to eliminate duplicate DB queries.
    4. Does NOT preload staff court assignments. Court is resolved only when
       explicitly targeted.
    """
    cached = getattr(request, "_access_context", None)
    if cached is not None and cached.club and cached.club.slug == club_slug:
        if court_id is None:
            return cached
        if cached.court and str(cached.court.id) == str(court_id):
            return cached

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        raise NotAuthenticated("Authentication credentials were not provided.")

    club = get_object_or_404(Club, slug=club_slug)
    is_platform_admin = bool(user.is_platform_super_admin())

    # Fetch active access-granting memberships for this user in this club
    memberships = list(
        ClubMembership.objects.granting_access()
        .filter(club=club, user=user)
        .select_related("club", "user")
        .order_by("id")
    )

    if not is_platform_admin and not memberships:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message=CLUB_ACCESS_REVOKED_MESSAGE,
            details={"club_slug": club.slug},
        )

    resolved_court = None
    if is_platform_admin:
        role = Role.ADMIN
        membership = memberships[0] if memberships else None
    else:
        primary_membership = min(
            memberships,
            key=lambda m: ROLE_PRECEDENCE.get(m.role, 99),
        )
        membership = primary_membership
        role = primary_membership.role

    # Resolve Court ONLY when explicitly requested via URL parameters
    if court_id is not None:
        from apps.courts.models import Court

        resolved_court = get_object_or_404(Court, club=club, pk=court_id)

    context = RequestAccessContext(
        user=user,
        role=role,
        club=club,
        membership=membership,
        court=resolved_court,
        is_platform_admin=is_platform_admin,
    )

    request._access_context = context
    request.access_context = context
    return context


def resolve_global_scope(request) -> RequestAccessContext:
    """
    Resolve non-club platform access context (e.g. global user management).
    """
    cached = getattr(request, "_access_context", None)
    if cached is not None and cached.club is None:
        return cached

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        raise NotAuthenticated("Authentication credentials were not provided.")

    is_platform_admin = bool(user.is_platform_super_admin())
    role = Role.ADMIN if is_platform_admin else "USER"

    context = RequestAccessContext(
        user=user,
        role=role,
        club=None,
        membership=None,
        court=None,
        is_platform_admin=is_platform_admin,
    )

    request._access_context = context
    request.access_context = context
    return context
