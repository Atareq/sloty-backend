"""
Players service layer.

All public functions are pure — they perform no role checks. Role enforcement
lives in ViewSets and the authorization matrix. Services own transactions,
multi-model writes, and data integrity invariants.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.exceptions import SlotyAPIException
from apps.players.models import ClubPlayer, PlayerProfile


def find_or_create_player_profile(
    phone_number: str,
    full_name: str = "",
) -> tuple[PlayerProfile, bool]:
    """
    Find an existing PlayerProfile by phone, or create a new one.

    Phone number is the global identity key. If a profile already exists,
    the existing record is returned unchanged — callers must not assume that
    passing full_name will update an existing profile's name.

    Returns:
        (PlayerProfile, created: bool)
    """
    with transaction.atomic():
        profile, created = PlayerProfile.objects.get_or_create(
            phone_number=phone_number,
            defaults={"full_name": full_name},
        )
    return profile, created


def get_or_create_club_player(
    club,
    player_profile: PlayerProfile,
    display_name: str = "",
    player_number: int | None = None,
) -> tuple[ClubPlayer, bool]:
    """
    Find the ACTIVE ClubPlayer for (club, player_profile), or create one.

    Each club's labeling of a player is independent. Two clubs may register
    the same PlayerProfile with completely different display names.

    ClubPlayer is append-oriented and historical (see
    apps/players/AGENTS.md "ClubPlayer Lifecycle"): only rows with
    deleted_at IS NULL are considered "the current identity" for a
    (club, player_profile) pair. A superseded (soft-deleted) row is never
    returned and never resurrected here — if no active row exists (either
    because none was ever created, or because the only prior row for this
    pair was superseded via supersede_club_player()), a brand-new active
    row is created instead.

    Returns:
        (ClubPlayer, created: bool)
    """
    club_player = ClubPlayer.objects.filter(
        club=club, player_profile=player_profile, deleted_at__isnull=True
    ).first()
    if club_player is not None:
        return club_player, False

    # Mirrors Django's own get_or_create() race-safety pattern: the
    # conditional unique constraint (unique_active_club_player_profile) can
    # still raise IntegrityError if a concurrent request creates the active
    # row between the filter() above and this create().
    try:
        with transaction.atomic():
            club_player = ClubPlayer.objects.create(
                club=club,
                player_profile=player_profile,
                display_name=display_name,
                player_number=player_number,
            )
        return club_player, True
    except IntegrityError:
        return (
            ClubPlayer.objects.get(
                club=club, player_profile=player_profile, deleted_at__isnull=True
            ),
            False,
        )


def supersede_club_player(
    club_player: ClubPlayer,
    display_name: str = "",
    player_number: int | None = None,
) -> ClubPlayer:
    """
    Replace an active ClubPlayer with a new version, preserving history.

    ClubPlayer is an append-oriented historical identity record — it is
    never edited in place (see apps/players/AGENTS.md "ClubPlayer Lifecycle").
    When a player's club-local display_name/player_number changes, this is
    the ONLY supported transition:

        1. Soft-delete `club_player` (deleted_at = now).
        2. Create a brand-new active ClubPlayer for the same
           (club, player_profile) pair with the new display_name/player_number.

    Any Booking that already references `club_player` keeps pointing at it —
    that FK *is* the historical snapshot as of when the booking was made.
    Callers needing the "current" identity going forward must use the
    returned new ClubPlayer (or re-resolve via get_or_create_club_player()).

    Raises:
        ValueError: if `club_player` is already soft-deleted — you cannot
            supersede a row that is not the active identity.

    Returns:
        The newly created active ClubPlayer.
    """
    with transaction.atomic():
        locked = ClubPlayer.objects.select_for_update().get(pk=club_player.pk)
        if locked.deleted_at is not None:
            raise ValueError(
                "Cannot supersede ClubPlayer "
                f"{locked.pk}: it is already soft-deleted (deleted_at="
                f"{locked.deleted_at!r}). Only the active ClubPlayer for a "
                "(club, player_profile) pair may be superseded."
            )
        locked.deleted_at = timezone.now()
        locked.save(update_fields=["deleted_at", "updated_at"])

        new_club_player = ClubPlayer.objects.create(
            club_id=locked.club_id,
            player_profile_id=locked.player_profile_id,
            display_name=display_name,
            player_number=player_number,
        )
    return new_club_player


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
            # Already linked to the same user — idempotent.
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
