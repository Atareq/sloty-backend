"""
Players service layer.

All public functions are pure — they perform no role checks. Role enforcement
lives in ViewSets and the authorization matrix. Services own transactions,
multi-model writes, and data integrity invariants.
"""

from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery

from apps.common.exceptions import SlotyAPIException
from apps.players.models import ClubPlayer, PlayerProfile


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
            defaults={"full_name": full_name},
        )
    return profile, created


def get_current_club_player(club, player_profile) -> ClubPlayer | None:
    """Return the current ClubPlayer version for (club, player_profile), or None."""
    return (
        ClubPlayer.objects.filter(
            club=club,
            player_profile=player_profile,
            is_current_version=True,
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


def get_last_used_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the ClubPlayer version last attached to a Booking for this
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
    display_name: str = "",
    player_number: int | None = None,
) -> tuple[ClubPlayer, bool]:
    """
    Return the current ClubPlayer version for (club, player_profile), or
    create the first version.

    Each club's labeling of a player is independent. Two clubs may register
    the same PlayerProfile with completely different display names.

    If a current version already exists, it is returned unchanged. A
    different display_name/player_number on this call does NOT create a
    new version — booking default resolution must use the current version.
    Identity changes go through create_club_player_version().

    If no current version exists, a first version is created with the given
    display_name/player_number, is_current_version=True, previous_version=None.

    Returns:
        (ClubPlayer, created: bool)
    """
    current = get_current_club_player(club, player_profile)
    if current is not None:
        return current, False

    try:
        with transaction.atomic():
            club_player = ClubPlayer.objects.create(
                club=club,
                player_profile=player_profile,
                display_name=display_name,
                player_number=player_number,
                previous_version=None,
                is_current_version=True,
            )
        return club_player, True
    except IntegrityError:
        current = get_current_club_player(club, player_profile)
        if current is None:
            raise
        return current, False


def create_club_player_version(
    club_player: ClubPlayer,
    display_name: str = "",
    player_number: int | None = None,
) -> ClubPlayer:
    """
    Replace the current ClubPlayer version with a new one, preserving history.

    Atomic:
      1. Lock the current row.
      2. Mark it is_current_version=False.
      3. Create a new row with previous_version pointing at the old row,
         is_current_version=True, and the new display_name/player_number.

    Identity fields on the old row are never mutated. Existing Booking FKs
    keep pointing at the old row. The old row has no updated_at — flipping
    is_current_version is a lifecycle pointer, not an identity edit.

    If the locked row already has this exact display_name and player_number,
    no new version is created — the current row is returned. This keeps
    POSTing the same roster payload idempotent.

    Raises:
        ValueError: if `club_player` is not the current version.
    """
    with transaction.atomic():
        locked = ClubPlayer.objects.select_for_update().get(pk=club_player.pk)
        if not locked.is_current_version:
            raise ValueError(
                "Cannot create a new ClubPlayer version from "
                f"{locked.pk}: it is not the current version. Pass the "
                "current ClubPlayer for this (club, player_profile) pair."
            )
        if (
            locked.display_name == display_name
            and locked.player_number == player_number
        ):
            return locked

        locked.is_current_version = False
        locked.save(update_fields=["is_current_version"])

        new_club_player = ClubPlayer.objects.create(
            club_id=locked.club_id,
            player_profile_id=locked.player_profile_id,
            display_name=display_name,
            player_number=player_number,
            previous_version=locked,
            is_current_version=True,
        )
    return new_club_player


def get_preferred_club_player(club, player_profile) -> ClubPlayer | None:
    """
    Return the recommended ClubPlayer version for (club, player_profile).

    Last-used wins: the club_player on this person's latest Booking at
    this club (matched via player_profile_id). If they have never been
    booked here, fall back to the current roster version.

    Bookings are historical events and never rewrite ClubPlayer rows.
    New booking *creation* still defaults to the current version unless
    an explicit club_player_id is supplied.

    Returns None if no ClubPlayer exists yet for this pair.
    """
    last_used = get_last_used_club_player(club, player_profile)
    if last_used is not None:
        return last_used
    return get_current_club_player(club, player_profile)


def link_player_to_user(player_profile: PlayerProfile, user) -> PlayerProfile:
    """
    Link a PlayerProfile to an authenticated User account.

    Idempotent: no-op if already linked to the same user.
    Raises SlotyAPIException if the profile is already linked to a different user
    (account-linking conflict must be resolved explicitly — not silently overwritten).

    Returns the updated PlayerProfile.
    """
    if player_profile.user_id is not None:
        if player_profile.user_id == user.pk:
            return player_profile
        raise SlotyAPIException(
            status_code=409,
            code="PLAYER_ALREADY_LINKED",
            message=(
                "This player profile is already linked to a different user account."
            ),
        )
    with transaction.atomic():
        player_profile.user = user
        player_profile.save(update_fields=["user", "updated_at"])
    return player_profile
