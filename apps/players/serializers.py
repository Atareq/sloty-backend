"""
Players serializers.

Read serializers never expose the user FK directly (privacy boundary — callers
should not need to know which User account a player is linked to).
Write serializers use phone_number as the player identity key; the club and
player_profile FKs are always injected by the view layer, never accepted from
request bodies.

ClubPlayer has no update/partial_update write serializer. It is a
permanent, versioned historical identity record — a new display_name/
player_number is recorded by POSTing a new version (see
apps.players.services.create_club_player_version()), not by an in-place
PATCH. See apps/players/AGENTS.md "ClubPlayer Lifecycle (Versioned,
Append-Only)".
"""

from rest_framework import serializers

from apps.players.models import ClubPlayer, PlayerProfile


class PlayerProfileSerializer(serializers.ModelSerializer):
    """
    Read serializer for PlayerProfile.

    Exposes has_account (bool) rather than the raw user FK to preserve the
    privacy boundary between authentication identity and player identity.
    full_name is the player's own preferred personal name — not a club label.
    """

    has_account = serializers.SerializerMethodField(
        help_text="True if this player profile is linked to a user account."
    )

    class Meta:
        model = PlayerProfile
        fields = (
            "id",
            "phone_number",
            "full_name",
            "verified",
            "has_account",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_has_account(self, obj) -> bool:
        return obj.user_id is not None


class PlayerProfileCreateSerializer(serializers.Serializer):
    """
    Write serializer for finding-or-creating a PlayerProfile.

    Used by PlayerProfileViewSet.create. The view delegates to the service
    layer to perform the actual find-or-create logic.
    """

    phone_number = serializers.CharField(
        help_text="E.164 phone number (e.g. +201012345678)."
    )
    full_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Optional preferred personal name. Only used if a new profile "
            "is created; never overwrites an existing profile."
        ),
    )

    def to_representation(self, instance):
        return PlayerProfileSerializer(instance, context=self.context).data


class ClubPlayerVersionSerializer(serializers.ModelSerializer):
    """Slim read serializer for a ClubPlayer version nested under a profile."""

    class Meta:
        model = ClubPlayer
        fields = (
            "id",
            "display_name",
            "player_number",
            "previous_version",
            "is_current_version",
            "created_at",
        )
        read_only_fields = fields


class ClubPlayerSerializer(serializers.ModelSerializer):
    """
    Read serializer for ClubPlayer with nested PlayerProfile.
    """

    player_profile = PlayerProfileSerializer(read_only=True)

    class Meta:
        model = ClubPlayer
        fields = (
            "id",
            "player_profile",
            "display_name",
            "player_number",
            "previous_version",
            "is_current_version",
            "created_at",
        )
        read_only_fields = fields


class ClubPlayerCreateSerializer(serializers.Serializer):
    """
    Write serializer for creating a ClubPlayer version.

    The caller supplies phone_number to identify (or create) the global
    PlayerProfile. club and player_profile FKs are never accepted from the
    request body — they are injected by perform_create.

    POSTing a new display_name/player_number for an existing current version
    records a new version rather than mutating the old one.
    """

    phone_number = serializers.CharField(
        help_text=(
            "Phone number of the player. Used to find-or-create the global "
            "PlayerProfile."
        )
    )
    full_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Optional preferred personal name. Only used if a new "
            "PlayerProfile is created."
        ),
    )
    display_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Club-specific display name for this version. Blank uses the "
            "global full_name. Independent from PlayerProfile.full_name."
        ),
    )
    player_number = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        min_value=0,
        max_value=32767,
        help_text="Optional club-internal jersey or member number.",
    )

    def to_representation(self, instance):
        return ClubPlayerSerializer(instance, context=self.context).data


class PlayerProfileWithVersionsSerializer(PlayerProfileSerializer):
    """
    Player search/list contract: global profile + this club's ClubPlayer
    versions + the recommended version id.

    Recommendation is last-used: the ClubPlayer on this person's latest
    Booking at this club (player_profile_id match). If they have never
    been booked here, fall back to the current roster version.
    """

    club_player_versions = ClubPlayerVersionSerializer(
        many=True, read_only=True, source="club_versions"
    )
    recommended_club_player_id = serializers.SerializerMethodField()

    class Meta(PlayerProfileSerializer.Meta):
        fields = PlayerProfileSerializer.Meta.fields + (
            "club_player_versions",
            "recommended_club_player_id",
        )

    def get_recommended_club_player_id(self, obj) -> int | None:
        last_used_id = getattr(obj, "last_used_club_player_id", None)
        if last_used_id is not None:
            return last_used_id
        versions = getattr(obj, "club_versions", None)
        if versions is None:
            return None
        current = next((v for v in versions if v.is_current_version), None)
        return current.id if current is not None else None
