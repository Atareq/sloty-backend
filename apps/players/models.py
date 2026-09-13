"""
PlayerProfile — global customer identity (keyed by phone number).
ClubPlayer    — club-local representation of a player.

Identity boundary:
  PlayerProfile is GLOBAL — no authorization_config. Phone uniqueness is the
  identity constraint; it is not club-scoped at the row level. Access is gated
  by club membership at the API layer (club membership required to call any
  endpoint), but the profile row itself is shared across clubs.

  ClubPlayer IS club-scoped. It declares authorization_config with
  default_scope="club" and is served exclusively through the Authorization
  Spine v2 (SlotyScopedResourceMixin + scoped_queryset). Clubs cannot read or
  write each other's ClubPlayer rows.

  ClubPlayer is append-oriented and historical, not a normal editable entity:
  identity changes create a new row (see supersede_club_player() in
  apps/players/services.py) rather than mutating an existing one. See the
  ClubPlayer docstring below for the full lifecycle and enforcement model.
"""

from django.conf import settings
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from apps.clubs.models import Club


class PlayerProfile(models.Model):
    """
    Global customer identity record.

    A player does not need a user account. Phone number is the unique identity
    key. A user FK may be populated later to link the player to an account
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
            "Optional. Clubs may override with their own display_name via ClubPlayer."
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
    entirely (it issues a bulk SQL UPDATE without ever instantiating/calling
    save() on matched rows). This override closes that gap so
    `ClubPlayer.objects.filter(...).update(display_name=...)` is rejected the
    same way `instance.save()` is.
    """

    _MUTABLE_FIELDS = frozenset({"deleted_at", "updated_at"})

    def update(self, **kwargs):
        if not set(kwargs).issubset(self._MUTABLE_FIELDS):
            raise ValueError(
                "Bulk update() of ClubPlayer is restricted to "
                f"{sorted(self._MUTABLE_FIELDS)}. ClubPlayer rows are "
                "immutable historical identity records after creation — use "
                "apps.players.services.supersede_club_player() to change "
                "identity information (it soft-deletes this row and creates "
                "a new one)."
            )
        return super().update(**kwargs)

    def active(self):
        """Rows that are the current, non-superseded identity for their pair."""
        return self.filter(deleted_at__isnull=True)


class ClubPlayer(models.Model):
    """
    Club-local representation of a player — an append-oriented historical
    identity record, not a normal editable entity.

    Each club controls its own naming and numbering of a player independently.
    The same PlayerProfile may have different display_name and player_number
    values at different clubs — both are valid and expected.

    Historical/append-only lifecycle:
      A ClubPlayer row represents the identity state used by bookings made
      while it was active. When a player's club-local name/number changes,
      the existing row is never edited in place — instead
      apps.players.services.supersede_club_player() soft-deletes it
      (deleted_at) and creates a brand-new active row for the same
      (club, player_profile) pair. Bookings that already reference the old
      row keep pointing at it — that FK *is* the historical snapshot. See
      apps/players/AGENTS.md "ClubPlayer Lifecycle (Append-Only)".

    Immutability enforcement (defense in depth, see save()/ClubPlayerQuerySet):
      - API layer: ClubPlayerViewSet exposes no update/partial_update action.
      - Model layer: save() rejects any in-place field change on an existing
        row unless the caller explicitly restricts update_fields to
        {"deleted_at", "updated_at"} (the only lifecycle transition allowed
        post-creation: soft delete).
      - QuerySet layer: ClubPlayerQuerySet.update() applies the same
        restriction to bulk updates, which bypass save() entirely.

    Authorization Spine v2: default_scope="club". The queryset is always
    bounded to the authenticated user's club; cross-club row access is
    structurally impossible via scoped_queryset.
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
            "Club-specific name override. Blank means the global full_name is used."
        ),
    )
    player_number = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Club-internal jersey or member number.",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Soft-delete marker. NULL means this is the current/active "
            "identity row for its (club, player_profile) pair. Set only by "
            "apps.players.services.supersede_club_player() when this row's "
            "identity information is replaced by a new ClubPlayer row. A "
            "deleted row is never physically removed and never resurrected "
            "— it remains permanently as the historical identity referenced "
            "by any Booking that was created while it was active."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ClubPlayerQuerySet.as_manager()

    # save() forbids changing club/player_profile/display_name/player_number
    # on an existing row except through this explicit update_fields allowlist.
    _MUTABLE_UPDATE_FIELDS = frozenset({"deleted_at", "updated_at"})

    # Authorization Spine v2 resource-query contract.
    # ClubPlayer is club-scoped only — no court scope.
    # See apps/common/authorization/contracts.py for the required shape.
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
                condition=models.Q(deleted_at__isnull=True),
                name="unique_active_club_player_profile",
            ),
        ]
        indexes = [
            models.Index(fields=["club"]),
            models.Index(fields=["player_profile"]),
            models.Index(fields=["club", "player_profile"]),
            models.Index(fields=["deleted_at"]),
        ]

    def __str__(self) -> str:
        label = self.display_name or str(self.player_profile)
        suffix = " [deleted]" if self.deleted_at is not None else ""
        return f"ClubPlayer({self.club_id}, {label}){suffix}"

    @property
    def is_active(self) -> bool:
        """True if this row is the current identity (not superseded/soft-deleted)."""
        return self.deleted_at is None

    def save(self, *args, **kwargs):
        """
        Block in-place mutation of an existing ClubPlayer.

        New rows (self.pk is None) save normally — this only guards updates
        to a row that already exists in the database. Existing rows may only
        be saved with an explicit update_fields restricted to
        {"deleted_at", "updated_at"} — the sole allowed post-creation
        transition (soft delete). Any other update path (including a bare
        `instance.save()` after mutating display_name/player_number/club/
        player_profile) raises ValueError. Use
        apps.players.services.supersede_club_player() to change identity
        information — it creates a new row instead of mutating this one.
        """
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields).issubset(
                self._MUTABLE_UPDATE_FIELDS
            ):
                allowed = sorted(self._MUTABLE_UPDATE_FIELDS)
                raise ValueError(
                    "ClubPlayer identity fields (club, player_profile, "
                    "display_name, player_number) are immutable after "
                    "creation. save() on an existing ClubPlayer must pass "
                    f"update_fields restricted to {allowed}. Use "
                    "apps.players.services.supersede_club_player() to "
                    "change identity information."
                )
        super().save(*args, **kwargs)
