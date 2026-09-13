"""
Operational roles for the Sloty authorization engine.

Grounds roles in ClubMembership.Role model constants while providing
a platform ADMIN role for cross-club super administrators.
"""

from apps.clubs.models import ClubMembership


class Role:
    """Standard role identifiers for authorization matrix lookups."""

    ADMIN = "ADMIN"
    OWNER = ClubMembership.Role.OWNER.value  # "OWNER"
    MANAGER = ClubMembership.Role.MANAGER.value  # "MANAGER"
    STAFF = ClubMembership.Role.STAFF.value  # "STAFF"

    ALL = (ADMIN, OWNER, MANAGER, STAFF)
    CLUB_ROLES = (OWNER, MANAGER, STAFF)
