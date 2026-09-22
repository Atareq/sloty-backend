"""
Booking customer identity for operational consumers.

ClubPlayer is the exact version stored on the booking. Display and search
read through Booking.club_player → PlayerProfile. There is no Booking
customer snapshot and no fallback.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from phonenumber_field.phonenumber import to_python
from rest_framework import status

from apps.common.exceptions import SlotyAPIException
from apps.common.search import customer_phone_search_q
from apps.players.models import ClubPlayer
from apps.players.services import find_or_create_player_profile

BOOKING_IDENTITY_SELECT_RELATED = ("club_player__player_profile",)

BOOKING_CLUB_PLAYER_NOT_FOUND_MESSAGE = "The requested club player was not found."
BOOKING_CLUB_PLAYER_MISMATCH_MESSAGE = (
    "The requested club player belongs to a different club."
)
BOOKING_CLUB_PLAYER_PHONE_MISMATCH_MESSAGE = (
    "The requested club player does not match the customer phone number."
)
BOOKING_CLUB_PLAYER_NAME_MISMATCH_MESSAGE = (
    "The requested club player does not match the customer name."
)
BOOKING_CUSTOMER_IDENTITY_INCOMPLETE_MESSAGE = (
    "customer_phone is required when club_player_id is not provided."
)


def _normalize_customer_phone(customer_phone) -> str:
    phone_obj = to_python(customer_phone)
    if phone_obj and phone_obj.is_valid():
        return phone_obj.as_e164
    return str(customer_phone or "").strip()


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


def resolve_club_player(
    *,
    club,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    club_player_id: int | None = None,
) -> ClubPlayer:
    """
    Resolve ClubPlayer identity: phone → PlayerProfile, then
    club + PlayerProfile + exact display_name → ClubPlayer.

    club_player_id, when supplied, reuses that row after club/phone/name
    checks and never creates or mutates a ClubPlayer.
    """
    if club_player_id is not None:
        try:
            club_player = ClubPlayer.objects.select_related(
                "club", "player_profile"
            ).get(pk=club_player_id)
        except ClubPlayer.DoesNotExist as exc:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="BOOKING_CLUB_PLAYER_NOT_FOUND",
                message=BOOKING_CLUB_PLAYER_NOT_FOUND_MESSAGE,
            ) from exc
        if club_player.club_id != club.id:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="BOOKING_CLUB_PLAYER_MISMATCH",
                message=BOOKING_CLUB_PLAYER_MISMATCH_MESSAGE,
            )
        if customer_phone is not None:
            cp_phone = club_player.player_profile.phone_number
            existing_phone = (
                cp_phone.as_e164
                if getattr(cp_phone, "as_e164", None)
                else str(cp_phone or "").strip()
            )
            if existing_phone != _normalize_customer_phone(customer_phone):
                raise SlotyAPIException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="BOOKING_CLUB_PLAYER_PHONE_MISMATCH",
                    message=BOOKING_CLUB_PLAYER_PHONE_MISMATCH_MESSAGE,
                )
        if customer_name is not None:
            submitted_name = customer_name.strip()
            expected_name = (club_player.display_name or "").strip()
            if submitted_name != expected_name:
                raise SlotyAPIException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="BOOKING_CLUB_PLAYER_MISMATCH",
                    message=BOOKING_CLUB_PLAYER_NAME_MISMATCH_MESSAGE,
                )
        return club_player

    if not customer_phone:
        raise SlotyAPIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="BOOKING_CUSTOMER_IDENTITY_INCOMPLETE",
            message=BOOKING_CUSTOMER_IDENTITY_INCOMPLETE_MESSAGE,
        )

    normalized_phone = _normalize_customer_phone(customer_phone)
    submitted_name = (customer_name or "").strip()
    player_profile, _ = find_or_create_player_profile(
        phone_number=normalized_phone,
        full_name=submitted_name,
    )

    club_player = (
        ClubPlayer.objects.select_related("player_profile")
        .filter(
            club=club,
            player_profile=player_profile,
            display_name=submitted_name,
        )
        .first()
    )
    if club_player is not None:
        return club_player

    try:
        with transaction.atomic(savepoint=True):
            club_player, _ = ClubPlayer.objects.get_or_create(
                club=club,
                player_profile=player_profile,
                display_name=submitted_name,
            )
        return club_player
    except (IntegrityError, ValidationError):
        return ClubPlayer.objects.select_related("player_profile").get(
            club=club,
            player_profile=player_profile,
            display_name=submitted_name,
        )
