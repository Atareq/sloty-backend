"""Stable API error codes.

Every value here is a public API key: clients branch on it and the frontend maps
it to Arabic copy. Renaming a member is a breaking change — add a new one instead.

Codes are grouped by the layer that raises them. Domain-specific business codes
(``BOOKING_SLOT_UNAVAILABLE``, ``CLUB_ACCESS_REVOKED``, …) stay close to the
domain that owns them; this module holds the cross-cutting ones produced by the
exception handler itself.
"""


class ErrorCode:
    """Namespace of cross-cutting error codes. Not instantiated."""

    # Generic
    API_ERROR = "API_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"

    # Authentication and session lifecycle.
    # See apps/common/AGENTS.md "Standard Authentication Error Codes".
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TOKEN_NOT_VALID = "TOKEN_NOT_VALID"
    USER_INACTIVE = "USER_INACTIVE"
    USER_DELETED = "USER_DELETED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
