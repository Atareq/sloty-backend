"""
Players service layer.

All public functions are pure — they perform no role checks. Role enforcement
lives in ViewSets and the authorization matrix. Services own transactions,
multi-model writes, and data integrity invariants.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from phonenumber_field.phonenumber import to_python

from apps.common.exceptions import SlotyAPIException
from apps.players.models import ClubPlayer, PlayerProfile
from apps.profiles.models import Profile


def find_or_create_player_profile(
    phone_number: str,
    full_name: str = "",
) -> tuple[PlayerProfile, bool]:
    """
    Find an existing PlayerProfile by phone, or create a new one.

    Phone number is the global identity key. Same phone always returns the
    same profile. Different phone always creates/returns a different
    profile. There is no name-based matching and no merging. If a profile
    already exists, the existing record is returned unchanged — callers
    must not assume that passing full_name will update an existing
    profile's preferred personal name.

    Returns:
        (PlayerProfile, created: bool)
    """
    phone_obj = to_python(phone_number)
    if phone_obj and phone_obj.is_valid():
        normalized_phone = phone_obj.as_e164
    else:
        normalized_phone = str(phone_number or "").strip()

    try:
        with transaction.atomic():
            profile, created = PlayerProfile.objects.get_or_create(
                phone_number=normalized_phone,
                defaults={"full_name": full_name, "role": Profile.Role.PLAYER},
            )
        return profile, created
    except (IntegrityError, ValidationError):
        try:
            return PlayerProfile.objects.get(phone_number=normalized_phone), False
        except PlayerProfile.DoesNotExist:
            raise


def get_current_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the last-used ClubPlayer for (club, player_profile).

    Recency is the latest Booking.club_player. If no booking exists,
    falls back to the latest ClubPlayer row for that club/profile.
    This is recommendation, not identity equality.
    """
    from apps.bookings.models import Booking

    booking = (
        Booking.objects.filter(
            club=club,
            club_player_id__isnull=False,
            club_player__player_profile=player_profile,
        )
        .select_related("club_player")
        .order_by("-created", "-id")
        .first()
    )
    if booking is not None:
        return booking.club_player

    return (
        ClubPlayer.objects.filter(
            club=club,
            player_profile=player_profile,
        )
        .order_by("-id")
        .first()
    )


def last_used_club_player_id_subquery(club):
    """
    Subquery: ClubPlayer id on the latest Booking for OuterRef PlayerProfile
    at this club. Recency is Booking.created, not a field on ClubPlayer.
    """
    from apps.bookings.models import Booking

    return Subquery(
        Booking.objects.filter(
            club=club,
            club_player_id__isnull=False,
            club_player__player_profile_id=OuterRef("pk"),
        )
        .order_by("-created", "-id")
        .values("club_player_id")[:1]
    )


def last_used_at_subquery(club):
    """
    Subquery: created datetime on the latest Booking for OuterRef ClubPlayer
    at this club. Recency is Booking.created, not a field on ClubPlayer.
    """
    from apps.bookings.models import Booking

    return Subquery(
        Booking.objects.filter(
            club=club,
            club_player_id=OuterRef("pk"),
        )
        .order_by("-created", "-id")
        .values("created")[:1]
    )


def get_last_used_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the ClubPlayer last attached to a Booking for this
    (club, player_profile).

    Latest booking is ordered by Booking.created, then id. The booking's
    club_player.player_profile_id is the match key — that ClubPlayer row
    is the last-used version. Returns None when this person has no
    club-scoped booking with a resolved club_player.
    """
    from apps.bookings.models import Booking

    booking = (
        Booking.objects.filter(
            club=club,
            club_player_id__isnull=False,
            club_player__player_profile=player_profile,
        )
        .select_related("club_player")
        .order_by("-created", "-id")
        .first()
    )
    if booking is None:
        return None
    return booking.club_player


def get_or_create_club_player(
    club,
    player_profile: PlayerProfile,
    display_name: str,
    player_number: int | None = None,
) -> tuple[ClubPlayer, bool]:
    """
    Return the ClubPlayer for the exact identity triple, or create it.

    Identity is (club, player_profile, display_name) after strip. A different
    exact name is a different ClubPlayer. Concurrent unique races reload the
    existing row.
    """
    submitted_name = (display_name or "").strip()
    existing = ClubPlayer.objects.filter(
        club=club,
        player_profile=player_profile,
        display_name=submitted_name,
    ).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic(savepoint=True):
            club_player, created = ClubPlayer.objects.get_or_create(
                club=club,
                player_profile=player_profile,
                display_name=submitted_name,
                defaults={"player_number": player_number},
            )
        return club_player, created
    except (IntegrityError, ValidationError):
        return (
            ClubPlayer.objects.get(
                club=club,
                player_profile=player_profile,
                display_name=submitted_name,
            ),
            False,
        )


def get_preferred_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the current/recommended ClubPlayer identity for (club, player_profile).
    """
    return get_current_club_player(club, player_profile)


def link_player_to_user(player_profile: PlayerProfile, user) -> PlayerProfile:
    """
    Link a PlayerProfile to an authenticated User account.

    Idempotent: no-op if already linked to the same user.
    Raises SlotyAPIException if the profile is already linked to a different user
    (account-linking conflict must be resolved explicitly — not silently overwritten).

    Returns the updated PlayerProfile.
    """
    with transaction.atomic():
        locked_profile = PlayerProfile.objects.select_for_update().get(
            pk=player_profile.pk
        )
        if locked_profile.role != Profile.Role.PLAYER:
            raise SlotyAPIException(
                status_code=409,
                code="PLAYER_PROFILE_ROLE_INVALID",
                message="This profile is not a player profile.",
            )
        if locked_profile.user_id is not None:
            if locked_profile.user_id == user.pk:
                return locked_profile
            raise SlotyAPIException(
                status_code=409,
                code="PLAYER_ALREADY_LINKED",
                message=(
                    "This player profile is already linked to a different "
                    "user account."
                ),
            )
        if getattr(user, "profile", None) is not None:
            raise SlotyAPIException(
                status_code=409,
                code="USER_ALREADY_HAS_PROFILE",
                message="This user account is already linked to a profile.",
            )
        locked_profile.user = user
        locked_profile.save(update_fields=["user", "modified"])
    return locked_profile
