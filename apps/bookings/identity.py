"""
Booking customer identity for operational consumers.

ClubPlayer is the exact version stored on the booking. Display and search
read through Booking.club_player → PlayerProfile. There is no Booking
customer snapshot and no fallback.
"""

from django.db.models import Q

from apps.common.search import customer_phone_search_q

BOOKING_IDENTITY_SELECT_RELATED = ("club_player__player_profile",)


def booking_customer_display_name(booking) -> str:
    """Operational display name from the attached ClubPlayer version."""
    club_player = getattr(booking, "club_player", None)
    if club_player is None:
        return ""
    display_name = (club_player.display_name or "").strip()
    if display_name:
        return display_name
    profile = club_player.player_profile
    return (profile.full_name or "").strip() if profile is not None else ""


def booking_customer_display_phone(booking) -> str:
    """Operational display phone from PlayerProfile via ClubPlayer."""
    club_player = getattr(booking, "club_player", None)
    if club_player is None:
        return ""
    profile = club_player.player_profile
    if profile is not None and profile.phone_number:
        return str(profile.phone_number)
    return ""


def booking_identity_search_q(query: str, *, booking_prefix: str = "") -> Q:
    """Search ClubPlayer display name and PlayerProfile phone on the booking."""
    cleaned = (query or "").strip()
    if not cleaned:
        return Q()
    club_name = f"{booking_prefix}club_player__display_name"
    club_phone = f"{booking_prefix}club_player__player_profile__phone_number"
    return Q(**{f"{club_name}__icontains": cleaned}) | customer_phone_search_q(
        club_phone, cleaned
    )
