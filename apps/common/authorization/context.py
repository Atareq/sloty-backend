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
    profile: Optional[Any] = None
    owner_profile: Optional[Any] = None
    staff_profile: Optional[Any] = None
    court: Optional[Any] = None
    is_platform_admin: bool = False

    def __post_init__(self):
        if self.profile is None:
            raise ValueError("RequestAccessContext requires the current Profile.")

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user and getattr(self.user, "is_authenticated", False))
