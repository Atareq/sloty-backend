"""
Players service layer.

All public functions are pure — they perform no role checks. Role enforcement
lives in ViewSets and the authorization matrix. Services own transactions,
multi-model writes, and data integrity invariants.
"""

from django.db import transaction
from django.db.models import OuterRef, Subquery

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
    with transaction.atomic():
        profile, created = PlayerProfile.objects.get_or_create(
            phone_number=phone_number,
            defaults={"full_name": full_name, "role": Profile.Role.PLAYER},
        )
    return profile, created


def get_current_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the current ClubPlayer identity for (club, player_profile).

    The latest Booking.club_player relationship is the source of truth.
    If no booking exists for that player in that club, falls back to the
    latest ClubPlayer record for that player/club (or single record).
    """
    from apps.bookings.models import Booking

    booking = (
        Booking.objects.filter(
            club=club,
            club_player_id__isnull=False,
            club_player__player_profile=player_profile,
        )
        .select_related("club_player")
        .order_by("-created_at", "-id")
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
    at this club. Recency is Booking.created_at, not a field on ClubPlayer.
    """
    from apps.bookings.models import Booking

    return Subquery(
        Booking.objects.filter(
            club=club,
            club_player_id__isnull=False,
            club_player__player_profile_id=OuterRef("pk"),
        )
        .order_by("-created_at", "-id")
        .values("club_player_id")[:1]
    )


def get_last_used_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the ClubPlayer version last attached to a Booking for this
    (club, player_profile).

    Latest booking is ordered by Booking.created_at, then id. The booking's
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
        .order_by("-created_at", "-id")
        .first()
    )
    if booking is None:
        return None
    return booking.club_player


def get_or_create_club_player(
    club,
    player_profile: PlayerProfile,
    display_name: str = "",
    player_number: int | None = None,
) -> tuple[ClubPlayer, bool]:
    """
    Return the current ClubPlayer for (club, player_profile), or create one.

    If a current identity exists, it is returned unchanged.
    If none exists, a new ClubPlayer is created with the given
    display_name/player_number.

    Returns:
        (ClubPlayer, created: bool)
    """
    current = get_current_club_player(club, player_profile)
    if current is not None:
        return current, False

    with transaction.atomic():
        if player_profile.pk:
            PlayerProfile.objects.select_for_update().filter(
                pk=player_profile.pk
            ).first()
        current = get_current_club_player(club, player_profile)
        if current is not None:
            return current, False
        club_player = ClubPlayer.objects.create(
            club=club,
            player_profile=player_profile,
            display_name=display_name,
            player_number=player_number,
        )
    return club_player, True


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
        locked_profile.save(update_fields=["user", "modified_at", "modified_at"])
    return locked_profile
