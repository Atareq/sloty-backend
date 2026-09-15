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

from apps.clubs.models import Club
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.profiles.models import Profile

CLUB_ACCESS_REVOKED_MESSAGE = _("Your access to the selected club is no longer active.")


def _get_profile_scope(user):
    """Load the Profile graph once for authorization resolution."""
    return (
        Profile.objects.select_related("owner_profile", "staff_profile__court")
        .prefetch_related("owner_profile__clubs")
        .filter(user_id=user.pk)
        .first()
    )


def _owner_has_club(owner_profile, club) -> bool:
    return bool(
        owner_profile
        and any(owner_club.pk == club.pk for owner_club in owner_profile.clubs.all())
    )


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
    profile = _get_profile_scope(user)
    if profile is None:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message=CLUB_ACCESS_REVOKED_MESSAGE,
            details={"club_slug": club.slug},
        )

    role = profile.role
    is_platform_admin = role == Role.ADMIN
    owner_profile = getattr(profile, "owner_profile", None)
    staff_profile = getattr(profile, "staff_profile", None)
    if role == Role.OWNER and (
        owner_profile is None or not _owner_has_club(owner_profile, club)
    ):
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message=CLUB_ACCESS_REVOKED_MESSAGE,
            details={"club_slug": club.slug},
        )
    if role == Role.STAFF and (
        staff_profile is None or staff_profile.court.club_id != club.pk
    ):
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message=CLUB_ACCESS_REVOKED_MESSAGE,
            details={"club_slug": club.slug},
        )
    if role not in Role.ALL:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLUB_ACCESS_REVOKED",
            message=CLUB_ACCESS_REVOKED_MESSAGE,
            details={"club_slug": club.slug},
        )

    resolved_court = None

    # Resolve Court ONLY when explicitly requested via URL parameters
    if court_id is not None:
        from apps.courts.models import Court

        resolved_court = get_object_or_404(Court, club=club, pk=court_id)

    context = RequestAccessContext(
        user=user,
        role=role,
        club=club,
        profile=profile,
        owner_profile=owner_profile,
        staff_profile=staff_profile,
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

    profile = _get_profile_scope(user)
    if profile is None:
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="PROFILE_REQUIRED",
            message=_("An application profile is required."),
        )
    is_platform_admin = profile.role == Role.ADMIN

    context = RequestAccessContext(
        user=user,
        role=profile.role,
        club=None,
        profile=profile,
        owner_profile=getattr(profile, "owner_profile", None),
        staff_profile=getattr(profile, "staff_profile", None),
        court=None,
        is_platform_admin=is_platform_admin,
    )

    request._access_context = context
    request.access_context = context
    return context
