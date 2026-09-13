"""
PlayerProfile — global customer identity (keyed by phone number).
ClubPlayer    — club-local representation of a player, stored as immutable
                historical versions.

Identity boundary:
  PlayerProfile is GLOBAL — no authorization_config. Phone uniqueness is the
  identity constraint; it is not club-scoped at the row level. Access is gated
  by club membership at the API layer (club membership required to call any
  endpoint), but the profile row itself is shared across clubs.

  ClubPlayer IS club-scoped. It declares authorization_config with
  default_scope="club" and is served exclusively through the Authorization
  Spine v2 (SlotyScopedResourceMixin + scoped_queryset). Clubs cannot read or
  write each other's ClubPlayer rows.

  ClubPlayer is a permanent, versioned historical record — identity fields
  are never edited and the row is never deleted (no soft delete, no
  deleted_at). Identity changes create a new row, mark the previous row
  is_current_version=False, and point previous_version at that older row.
  At most one current version exists per (club, player_profile). Bookings
  store the exact ClubPlayer version used at creation time and are never
  rewritten when a newer version is recorded.

  Recency is not stored on ClubPlayer. The last-used version for a person
  at a club is the club_player on that player's latest Booking (matched
  via club_player.player_profile_id). See apps/players/AGENTS.md
  "ClubPlayer Lifecycle (Versioned, Append-Only)".
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
    via club membership checks.
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


class ClubPlayerQuerySet(models.QuerySet):
    """
    Enforces ClubPlayer immutability at the queryset level.

    Model.save() (below) blocks in-place mutation of identity fields for
    single-instance saves, but Django's QuerySet.update() bypasses save()
    entirely. This override closes that gap.
    """

    _MUTABLE_FIELDS = frozenset({"is_current_version"})

    def current(self):
        return self.filter(is_current_version=True)

    def update(self, **kwargs):
        if not set(kwargs).issubset(self._MUTABLE_FIELDS):
            raise ValueError(
                "Bulk update() of ClubPlayer is restricted to "
                f"{sorted(self._MUTABLE_FIELDS)}. ClubPlayer rows are "
                "permanent historical identity versions after creation — use "
                "apps.players.services.create_club_player_version() to record "
                "a new display_name/player_number (it creates a new row "
                "rather than mutating this one)."
            )
        return super().update(**kwargs)


class ClubPlayer(models.Model):
    """
    Club-local representation of a player — an immutable versioned
    historical record, not a normal editable entity.

    Each club controls its own naming and numbering of a player independently.
    The same PlayerProfile may have different display_name and player_number
    values at different clubs — both are valid and expected.

    Versioned lifecycle (NOT soft delete):
      A ClubPlayer row is one version of "how this club knew this player."
      When display_name/player_number changes, the existing row is never
      edited and never deleted. create_club_player_version() marks the
      current row is_current_version=False and inserts a new current row
      with previous_version pointing at the old one. Bookings that already
      reference the old row keep pointing at it — that FK *is* the
      historical snapshot.

      There is no last_used_at and no updated_at. Identity fields never
      change, so an edit timestamp would be a lie. Recency is derived from
      Booking: the last-used version is the club_player on the latest
      Booking for this (club, player_profile).

    Default booking resolution uses the current version
    (is_current_version=True). Staff may explicitly select a historical
    version for a new booking; that booking then stores that exact
    version id.

    Immutability enforcement (defense in depth):
      - API: ClubPlayerViewSet exposes no update/partial_update. POSTing a
        new display_name/player_number for an existing phone+club records a
        new version via create_club_player_version().
      - Model: save() rejects in-place identity mutation unless
        update_fields is restricted to is_current_version.
      - QuerySet: ClubPlayerQuerySet.update() applies the same restriction.

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
            "Club-specific name for this version. Independent from "
            "PlayerProfile.full_name and never auto-synchronized with it."
        ),
    )
    player_number = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Club-internal jersey or member number for this version.",
    )
    previous_version = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="next_versions",
        help_text=(
            "The ClubPlayer version this row replaced. NULL on the first "
            "version for a (club, player_profile) pair."
        ),
    )
    is_current_version = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "True iff this is the current club-local identity for its "
            "(club, player_profile) pair. At most one current version is "
            "allowed per pair. Historical versions keep False forever."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ClubPlayerQuerySet.as_manager()

    _MUTABLE_UPDATE_FIELDS = frozenset({"is_current_version"})

    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
        },
        "default_scope": "club",
        "select_related": ("club", "player_profile"),
        "prefetch_related": (),
    }

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["club", "player_profile"],
                condition=models.Q(is_current_version=True),
                name="unique_current_club_player_profile",
            ),
        ]
        indexes = [
            models.Index(fields=["club"]),
            models.Index(fields=["player_profile"]),
            models.Index(fields=["club", "player_profile"]),
            models.Index(fields=["is_current_version"]),
        ]

    def __str__(self) -> str:
        label = self.display_name or str(self.player_profile)
        suffix = "" if self.is_current_version else " [historical]"
        return f"ClubPlayer({self.club_id}, {label}){suffix}"

    def save(self, *args, **kwargs):
        """
        Block in-place mutation of an existing ClubPlayer.

        Existing rows may only be saved with update_fields restricted to
        {is_current_version} — the current→historical transition. Identity
        field changes must go through create_club_player_version(). There
        is no last_used_at or updated_at: this row is not an editable
        entity.
        """
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields).issubset(
                self._MUTABLE_UPDATE_FIELDS
            ):
                allowed = sorted(self._MUTABLE_UPDATE_FIELDS)
                raise ValueError(
                    "ClubPlayer identity fields (club, player_profile, "
                    "display_name, player_number, previous_version) are "
                    "immutable after creation. save() on an existing "
                    "ClubPlayer must pass update_fields restricted to "
                    f"{allowed}. Use "
                    "apps.players.services.create_club_player_version() to "
                    "record a new display_name/player_number version."
                )
        super().save(*args, **kwargs)
