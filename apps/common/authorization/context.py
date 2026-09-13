"""
Request Access Context.

Factual container representing the authenticated identity, operational role,
club scope, and explicitly targeted court for a request.

ARCHITECTURAL INVARIANT:
This is a FACT CONTAINER ONLY.
It must NEVER contain endpoint authorization decisions (e.g. can_create_booking),
queryset construction, or domain business rules.
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class RequestAccessContext:
    user: Any
    role: str
    club: Optional[Any] = None
    membership: Optional[Any] = None
    profile: Optional[Any] = None
    profile_type: Optional[str] = None
    court: Optional[Any] = None
    is_platform_admin: bool = False

    def __post_init__(self):
        # Support forward-compatibility with future multi-profile architecture
        # while drawing from current ClubMembership records.
        if self.profile is None and self.membership is not None:
            object.__setattr__(self, "profile", self.membership)
        elif self.profile is None and self.is_platform_admin:
            object.__setattr__(self, "profile", self.user)

        if self.profile_type is None:
            object.__setattr__(self, "profile_type", self.role)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user and getattr(self.user, "is_authenticated", False))
