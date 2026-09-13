"""Stable resource-scope keys used by Authorization Spine v2."""

from enum import Enum


class ResourceScope(str, Enum):
    """Built-in scope keys understood by the generic queryset resolver."""

    NONE = "none"
    CLUB = "club"
    COURT = "court"


def normalize_scope_key(scope) -> str:
    """Return the stable string key for a built-in or future scope."""
    if isinstance(scope, ResourceScope):
        return scope.value
    if isinstance(scope, str) and scope.strip():
        return scope.strip().lower()
    raise ValueError("Authorization scope keys must be non-empty strings.")
