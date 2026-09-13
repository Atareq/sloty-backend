"""
Booking customer identity for consumers.

ClubPlayer is the exact version stored on the booking. Operational display
and search should read through that FK. Snapshot columns remain on Booking
for write/create, idempotency, audit event-time JSON, and nullable-FK
fallback until they are removed in a later sprint.
"""

from django.db.models import Q

from apps.common.search import customer_phone_search_q

BOOKING_IDENTITY_SELECT_RELATED = ("club_player__player_profile",)


def booking_customer_display_name(booking) -> str:
    """
    Operational display name for a booking.

    Prefers the immutable ClubPlayer version attached at booking time.
    Falls back to Booking.customer_name when club_player is null or the
    version has a blank display_name.
    """
    club_player = getattr(booking, "club_player", None)
    if club_player is not None:
        display_name = (club_player.display_name or "").strip()
        if display_name:
            return display_name
        profile = getattr(club_player, "player_profile", None)
        if profile is not None:
            full_name = (profile.full_name or "").strip()
            if full_name:
                return full_name
    return (getattr(booking, "customer_name", None) or "").strip()


def booking_customer_display_phone(booking) -> str:
    """
    Operational display phone for a booking.

    Prefers PlayerProfile.phone_number via the attached ClubPlayer version.
    Falls back to Booking.customer_phone when club_player is null.
    """
    club_player = getattr(booking, "club_player", None)
    if club_player is not None:
        profile = getattr(club_player, "player_profile", None)
        if profile is not None and profile.phone_number:
            return str(profile.phone_number)
    phone = getattr(booking, "customer_phone", None)
    return str(phone) if phone else ""


def booking_identity_search_q(query: str, *, booking_prefix: str = "") -> Q:
    """
    Search snapshot columns and ClubPlayer identity together.

    Snapshot matches keep existing contracts working. ClubPlayer matches
    find the version stored on the booking (not a later current version).
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return Q()
    name = f"{booking_prefix}customer_name"
    snapshot_phone = f"{booking_prefix}customer_phone"
    club_name = f"{booking_prefix}club_player__display_name"
    club_phone = f"{booking_prefix}club_player__player_profile__phone_number"
    return (
        Q(**{f"{name}__icontains": cleaned})
        | Q(**{f"{club_name}__icontains": cleaned})
        | customer_phone_search_q(snapshot_phone, cleaned)
        | customer_phone_search_q(club_phone, cleaned)
    )
