"""
Unit tests for PlayerProfile and ClubPlayer models.

Tests cover:
  - Phone uniqueness (global identity constraint)
  - Nullable user FK (player without an account)
  - User linking via service
  - (club, player_profile) ACTIVE uniqueness (ClubPlayer constraint)
  - Cross-club independence (same profile, different club-local metadata)
  - Service layer: find_or_create, link_player_to_user conflict
  - ClubPlayer append-only historical lifecycle: soft delete, supersede
    service, active-uniqueness with historical versions, deleted-identity
    non-reuse, and model-level immutability enforcement (save()/update())
"""

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.services import (
    find_or_create_player_profile,
    get_or_create_club_player,
    link_player_to_user,
    supersede_club_player,
)


def make_user(username, **kwargs):
    return User.objects.create_user(
        username=username, password="test-pass-123", **kwargs
    )


def make_club(name, slug=None):
    return Club.objects.create(
        name=name,
        slug=slug or name.lower().replace(" ", "-"),
        governorate="ASSIUT",
        city="ASSIUT_MARKAZ",
    )


def make_profile(phone, full_name="", user=None):
    return PlayerProfile.objects.create(
        phone_number=phone, full_name=full_name, user=user
    )


class PlayerProfilePhoneUniquenessTests(TestCase):
    """Phone number is the global identity key — must be unique."""

    def test_duplicate_phone_raises_integrity_error(self):
        make_profile("+201012345678", full_name="Ahmed")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_profile("+201012345678", full_name="Different Person")

    def test_different_phones_create_distinct_profiles(self):
        p1 = make_profile("+201012345678")
        p2 = make_profile("+201012345679")
        self.assertNotEqual(p1.pk, p2.pk)

    def test_profile_str(self):
        p = make_profile("+201012345678")
        self.assertIn("201012345678", str(p))


class PlayerProfileUserLinkTests(TestCase):
    """User FK is nullable. A player can exist without an account."""

    def test_profile_without_user_is_valid(self):
        p = make_profile("+201012345678")
        self.assertIsNone(p.user)
        self.assertIsNone(p.user_id)

    def test_profile_with_user_is_valid(self):
        u = make_user("player-user")
        p = make_profile("+201012345678", user=u)
        self.assertEqual(p.user_id, u.pk)

    def test_link_player_to_user_service(self):
        u = make_user("link-user")
        p = make_profile("+201055556666")
        result = link_player_to_user(p, u)
        p.refresh_from_db()
        self.assertEqual(p.user_id, u.pk)
        self.assertEqual(result.pk, p.pk)

    def test_link_player_idempotent_same_user(self):
        u = make_user("idempotent-user")
        p = make_profile("+201055557777", user=u)
        # Calling again with the same user must not raise.
        result = link_player_to_user(p, u)
        self.assertEqual(result.user_id, u.pk)

    def test_link_player_conflict_raises_exception(self):
        u1 = make_user("user-one")
        u2 = make_user("user-two")
        p = make_profile("+201055558888", user=u1)
        with self.assertRaises(SlotyAPIException) as ctx:
            link_player_to_user(p, u2)
        self.assertEqual(ctx.exception.api_code, "PLAYER_ALREADY_LINKED")


class FindOrCreatePlayerProfileTests(TestCase):
    """Service: find_or_create_player_profile."""

    def test_creates_new_profile(self):
        p, created = find_or_create_player_profile("+201011112222", full_name="New")
        self.assertTrue(created)
        self.assertEqual(p.full_name, "New")

    def test_returns_existing_profile_without_overwriting_name(self):
        existing = make_profile("+201011113333", full_name="Original")
        p, created = find_or_create_player_profile(
            "+201011113333", full_name="Should Not Overwrite"
        )
        self.assertFalse(created)
        self.assertEqual(p.pk, existing.pk)
        # Name must be unchanged — existing record wins.
        self.assertEqual(p.full_name, "Original")

    def test_two_clubs_resolve_same_profile(self):
        """Global uniqueness: two separate callers for same phone get same profile."""
        p1, c1 = find_or_create_player_profile("+201099990000")
        p2, c2 = find_or_create_player_profile("+201099990000")
        self.assertEqual(p1.pk, p2.pk)
        self.assertTrue(c1)
        self.assertFalse(c2)


class ClubPlayerUniquenessTests(TestCase):
    """(club, player_profile) must be unique per club."""

    def setUp(self):
        self.club = make_club("Alpha Club", slug="alpha-club")
        self.profile = make_profile("+201022223333")

    def test_duplicate_club_player_raises_integrity_error(self):
        ClubPlayer.objects.create(club=self.club, player_profile=self.profile)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClubPlayer.objects.create(club=self.club, player_profile=self.profile)

    def test_different_clubs_can_have_same_profile(self):
        club_b = make_club("Beta Club", slug="beta-club")
        cp_a = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Mohamed"
        )
        cp_b = ClubPlayer.objects.create(
            club=club_b, player_profile=self.profile, display_name="Salah"
        )
        self.assertNotEqual(cp_a.pk, cp_b.pk)
        self.assertEqual(cp_a.player_profile_id, cp_b.player_profile_id)
        self.assertEqual(cp_a.display_name, "Mohamed")
        self.assertEqual(cp_b.display_name, "Salah")

    def test_club_player_str(self):
        cp = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo Salah",
        )
        self.assertIn("Mo Salah", str(cp))


class GetOrCreateClubPlayerTests(TestCase):
    """Service: get_or_create_club_player."""

    def setUp(self):
        self.club = make_club("Gamma Club", slug="gamma-club")
        self.profile = make_profile("+201033334444")

    def test_creates_club_player(self):
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="Player One", player_number=10
        )
        self.assertTrue(created)
        self.assertEqual(cp.display_name, "Player One")
        self.assertEqual(cp.player_number, 10)

    def test_returns_existing_club_player(self):
        existing = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Existing"
        )
        cp, created = get_or_create_club_player(self.club, self.profile)
        self.assertFalse(created)
        self.assertEqual(cp.pk, existing.pk)

    def test_deleted_club_player_is_not_returned_and_not_resurrected(self):
        """A soft-deleted ClubPlayer must never come back from get_or_create."""
        deleted = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Old Identity"
        )
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])

        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="New Identity"
        )
        self.assertTrue(created)
        self.assertNotEqual(cp.pk, deleted.pk)
        self.assertEqual(cp.display_name, "New Identity")
        self.assertTrue(cp.is_active)

    def test_returns_active_row_when_deleted_and_active_both_exist(self):
        deleted = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Old Identity"
        )
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])
        active = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Current Identity"
        )

        cp, created = get_or_create_club_player(self.club, self.profile)
        self.assertFalse(created)
        self.assertEqual(cp.pk, active.pk)


class ClubPlayerActiveUniquenessTests(TestCase):
    """
    ClubPlayer is append-oriented and historical: only one ACTIVE row per
    (club, player_profile) is allowed, but historical (deleted) versions may
    coexist with a current active row.
    """

    def setUp(self):
        self.club = make_club("Delta Club", slug="delta-club")
        self.profile = make_profile("+201044445555")

    def test_two_active_rows_for_same_pair_raises_integrity_error(self):
        ClubPlayer.objects.create(club=self.club, player_profile=self.profile)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClubPlayer.objects.create(club=self.club, player_profile=self.profile)

    def test_deleted_plus_active_for_same_pair_is_allowed(self):
        deleted = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Historical"
        )
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])

        # Must not raise — the deleted row does not occupy the active slot.
        active = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Current"
        )
        self.assertNotEqual(active.pk, deleted.pk)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            2,
        )

    def test_two_deleted_rows_for_same_pair_is_allowed(self):
        """Multiple historical versions may coexist; only ONE may be active."""
        first = ClubPlayer.objects.create(club=self.club, player_profile=self.profile)
        first.deleted_at = timezone.now()
        first.save(update_fields=["deleted_at", "updated_at"])

        second = ClubPlayer.objects.create(club=self.club, player_profile=self.profile)
        second.deleted_at = timezone.now()
        second.save(update_fields=["deleted_at", "updated_at"])

        # No exception — both are soft-deleted, neither occupies the active slot.
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile, deleted_at__isnull=False
            ).count(),
            2,
        )


class ClubPlayerImmutabilityTests(TestCase):
    """
    ClubPlayer identity fields cannot be mutated in place after creation —
    enforced at the model save() layer and the QuerySet.update() layer.
    """

    def setUp(self):
        self.club = make_club("Epsilon Club", slug="epsilon-club")
        self.profile = make_profile("+201055556000")
        self.cp = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Original Name",
            player_number=9,
        )

    def test_new_row_saves_normally(self):
        """The immutability guard only applies to rows that already have a pk."""
        cp = ClubPlayer(
            club=self.club,
            player_profile=make_profile("+201055556001"),
            display_name="Brand New",
        )
        cp.save()
        self.assertIsNotNone(cp.pk)

    def test_bare_save_on_existing_row_raises(self):
        self.cp.display_name = "Mutated Name"
        with self.assertRaises(ValueError):
            self.cp.save()

    def test_save_with_update_fields_on_identity_field_raises(self):
        self.cp.display_name = "Mutated Name"
        with self.assertRaises(ValueError):
            self.cp.save(update_fields=["display_name"])

    def test_save_with_update_fields_restricted_to_deleted_at_succeeds(self):
        now = timezone.now()
        self.cp.deleted_at = now
        # Must not raise — deleted_at/updated_at is the one allowed transition.
        self.cp.save(update_fields=["deleted_at", "updated_at"])
        self.cp.refresh_from_db()
        self.assertIsNotNone(self.cp.deleted_at)

    def test_queryset_update_on_identity_field_raises(self):
        with self.assertRaises(ValueError):
            ClubPlayer.objects.filter(pk=self.cp.pk).update(display_name="Hacked")
        self.cp.refresh_from_db()
        self.assertEqual(self.cp.display_name, "Original Name")

    def test_queryset_update_restricted_to_deleted_at_succeeds(self):
        ClubPlayer.objects.filter(pk=self.cp.pk).update(deleted_at=timezone.now())
        self.cp.refresh_from_db()
        self.assertIsNotNone(self.cp.deleted_at)

    def test_is_active_property(self):
        self.assertTrue(self.cp.is_active)
        self.cp.deleted_at = timezone.now()
        self.cp.save(update_fields=["deleted_at", "updated_at"])
        self.assertFalse(self.cp.is_active)


class SupersedeClubPlayerTests(TestCase):
    """Service: supersede_club_player — the only supported identity-change flow."""

    def setUp(self):
        self.club = make_club("Zeta Club", slug="zeta-club")
        self.profile = make_profile("+201066667000")
        self.original = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Ahmed Ali",
            player_number=7,
        )

    def test_supersede_soft_deletes_old_and_creates_new_active_row(self):
        new_cp = supersede_club_player(
            self.original, display_name="Ahmed Salah", player_number=11
        )

        self.original.refresh_from_db()
        self.assertIsNotNone(self.original.deleted_at)
        self.assertFalse(self.original.is_active)
        # The old row's identity data is untouched — only deleted_at changed.
        self.assertEqual(self.original.display_name, "Ahmed Ali")
        self.assertEqual(self.original.player_number, 7)

        self.assertTrue(new_cp.is_active)
        self.assertEqual(new_cp.display_name, "Ahmed Salah")
        self.assertEqual(new_cp.player_number, 11)
        self.assertEqual(new_cp.club_id, self.club.id)
        self.assertEqual(new_cp.player_profile_id, self.profile.id)
        self.assertNotEqual(new_cp.pk, self.original.pk)

    def test_supersede_leaves_exactly_one_active_row_for_the_pair(self):
        supersede_club_player(self.original, display_name="Ahmed Salah")
        active_count = ClubPlayer.objects.filter(
            club=self.club, player_profile=self.profile, deleted_at__isnull=True
        ).count()
        self.assertEqual(active_count, 1)
        total_count = ClubPlayer.objects.filter(
            club=self.club, player_profile=self.profile
        ).count()
        self.assertEqual(total_count, 2)

    def test_supersede_already_deleted_row_raises(self):
        supersede_club_player(self.original, display_name="Ahmed Salah")
        self.original.refresh_from_db()
        with self.assertRaises(ValueError):
            supersede_club_player(self.original, display_name="Third Version")

    def test_get_or_create_after_supersede_returns_new_row(self):
        new_cp = supersede_club_player(self.original, display_name="Ahmed Salah")
        cp, created = get_or_create_club_player(self.club, self.profile)
        self.assertFalse(created)
        self.assertEqual(cp.pk, new_cp.pk)
