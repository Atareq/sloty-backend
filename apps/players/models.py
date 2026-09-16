"""
PlayerProfile — global customer identity (keyed by phone number).
ClubPlayer    — club-local representation of a player.

Identity boundary:
  PlayerProfile is GLOBAL — no authorization_config. Phone uniqueness is the
  identity constraint; it is not club-scoped at the row level. Access is gated
  by Profile scope at the API layer (club access required to call any
  endpoint), but the player profile row itself is shared across clubs.

  ClubPlayer IS club-scoped. It declares authorization_config with
  default_scope="club" and is served exclusively through the Authorization
  Spine v2 (SlotyScopedResourceMixin + scoped_queryset). Clubs cannot read or
  write each other's ClubPlayer rows.

  The latest Booking.club_player relationship is the source of truth for the
  current ClubPlayer identity. If no booking exists yet for that player in that
  club, it falls back to the latest ClubPlayer record for that player/club.
  Historical Booking.club_player relationships remain intact.
"""

from django.conf import settings
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from apps.clubs.models import Club


class PlayerProfile(models.Model):
    """
    Global customer identity record.

    A player does not need a user account. Phone number is the unique identity
    key. Same phone = same PlayerProfile. Different phone = different
    PlayerProfile. There is no name-based matching, no automatic merging,
    and no identity transfer when a phone number changes.

    full_name is the player's own preferred personal name. Clubs do not
    control it and it is never auto-synchronized with ClubPlayer.display_name.

    A user FK may be populated later to link the player to an account
    (account linking phase — separate task).

    This model does NOT declare authorization_config because it has no club
    boundary at the DB row level. Access control is enforced at the API layer
    via Profile club-scope checks.
    """

    phone_number = PhoneNumberField(
        unique=True,
        db_index=True,
        help_text="Global identity key. Must be unique across all players.",
    )
    full_name = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Player's own preferred personal name. Clubs do not overwrite "
            "this; club-local naming lives on ClubPlayer.display_name and "
            "is never auto-synchronized with this field."
        ),
    )
    verified = models.BooleanField(
        default=False,
        help_text=(
            "Verification state is independent from authentication. "
            "A player is not verified until an explicit verification step runs."
        ),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="player_profiles",
        help_text=(
            "Optional link to an authenticated account. "
            "NULL means the player exists without an account."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone_number"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        return f"PlayerProfile({self.phone_number})"


class ClubPlayer(models.Model):
    """
    Club-local representation of a player.

    Each club controls its own naming and numbering of a player independently.
    The same PlayerProfile may have different display_name and player_number
    values at different clubs — both are valid and expected.

    The latest Booking.club_player relationship is the source of truth for the
    current ClubPlayer identity. If no booking exists yet for that player in that
    club, it falls back to the latest ClubPlayer record for that player/club.
    Historical Booking.club_player relationships remain intact.

    Authorization Spine v2: default_scope="club".
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="club_players",
    )
    player_profile = models.ForeignKey(
        PlayerProfile,
        on_delete=models.CASCADE,
        related_name="club_players",
    )
    display_name = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Club-specific name for this player. Independent from "
            "PlayerProfile.full_name and never auto-synchronized with it."
        ),
    )
    player_number = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Club-internal jersey or member number.",
    )

    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
        },
        "default_scope": "club",
        "select_related": ("club", "player_profile"),
        "prefetch_related": (),
    }

    class Meta:
        indexes = [
            models.Index(fields=["club"]),
            models.Index(fields=["player_profile"]),
            models.Index(fields=["club", "player_profile"]),
        ]

    def __str__(self) -> str:
        label = self.display_name or str(self.player_profile)
        return f"ClubPlayer({self.club_id}, {label})"
