"""
Application roles for the Sloty authorization engine.
"""

from apps.profiles.models import Profile


class Role:
    """Standard role identifiers for authorization matrix lookups."""

    ADMIN = "ADMIN"
    OWNER = Profile.Role.OWNER
    STAFF = Profile.Role.STAFF

    ALL = (ADMIN, OWNER, STAFF)
    CLUB_ROLES = (OWNER, STAFF)
