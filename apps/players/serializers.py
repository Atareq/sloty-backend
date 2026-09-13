"""
Players serializers.

Read serializers never expose the user FK directly (privacy boundary — callers
should not need to know which User account a player is linked to).
Write serializers use phone_number as the player identity key; the club and
player_profile FKs are always injected by the view layer, never accepted from
request bodies.

ClubPlayer has no update/partial_update write serializer. It is an
append-oriented historical identity record — identity changes go through
apps.players.services.supersede_club_player(), not an in-place PATCH. See
apps/players/AGENTS.md "ClubPlayer Lifecycle (Append-Only)".
"""

from rest_framework import serializers

from apps.players.models import ClubPlayer, PlayerProfile


class PlayerProfileSerializer(serializers.ModelSerializer):
    """
    Read serializer for PlayerProfile.

    Exposes has_account (bool) rather than the raw user FK to preserve the
    privacy boundary between authentication identity and player identity.
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
        help_text="Optional. Only used if a new profile is created.",
    )

    def to_representation(self, instance):
        return PlayerProfileSerializer(instance, context=self.context).data


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
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ClubPlayerCreateSerializer(serializers.Serializer):
    """
    Write serializer for creating a ClubPlayer.

    The caller supplies phone_number to identify (or create) the global
    PlayerProfile. club and player_profile FKs are never accepted from the
    request body — they are injected by perform_create.
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
        help_text="Optional. Only used if a new PlayerProfile is created.",
    )
    display_name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Club-specific display name for this player. Blank uses the "
            "global full_name."
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
