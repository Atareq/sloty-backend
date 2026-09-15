"""
Unit tests for PlayerProfile and ClubPlayer models.

Tests cover:
  - Phone uniqueness (global identity constraint)
  - Nullable user FK (player without an account)
  - User linking via service
  - ClubPlayer current-version uniqueness
    (UNIQUE(club, player_profile) WHERE is_current_version=True)
  - Cross-club independence (same profile, different club-local metadata)
  - Service layer: find_or_create, get_or_create_club_player,
    create_club_player_version, get_preferred_club_player,
    get_last_used_club_player, link_player_to_user conflict
  - ClubPlayer versioned/append-only lifecycle and immutability
"""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.services import (
    create_club_player_version,
    find_or_create_player_profile,
    get_last_used_club_player,
    get_or_create_club_player,
    get_preferred_club_player,
    link_player_to_user,
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


def make_court(club, name="Court 1"):
    return Court.objects.create(
        club=club,
        name=name,
        default_price=Decimal("300.00"),
        slot_duration_minutes=60,
    )


def make_booking(club, court, club_player, *, created=None):
    start = timezone.now()
    booking = Booking.objects.create(
        club=club,
        court=court,
        club_player=club_player,
        start_time=start,
        end_time=start + timedelta(hours=1),
        total_price=Decimal("300.00"),
    )
    if created is not None:
        Booking.objects.filter(pk=booking.pk).update(created=created)
        booking.refresh_from_db()
    return booking


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

    def test_creating_user_does_not_create_player_profile_or_club_player(self):
        user = make_user("account-without-player-profile")

        self.assertFalse(PlayerProfile.objects.filter(user=user).exists())
        self.assertFalse(ClubPlayer.objects.filter(player_profile__user=user).exists())

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
    """At most one current ClubPlayer per (club, player_profile)."""

    def setUp(self):
        self.club = make_club("Alpha Club", slug="alpha-club")
        self.profile = make_profile("+201022223333")

    def test_two_current_versions_for_same_pair_raises_integrity_error(self):
        ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo",
            is_current_version=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClubPlayer.objects.create(
                    club=self.club,
                    player_profile=self.profile,
                    display_name="Mohamed",
                    is_current_version=True,
                )

    def test_historical_plus_current_is_allowed(self):
        v1 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo",
            is_current_version=False,
        )
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mohamed",
            previous_version=v1,
            is_current_version=True,
        )
        self.assertNotEqual(v1.pk, v2.pk)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            2,
        )
        self.assertEqual(
            ClubPlayer.objects.current()
            .filter(club=self.club, player_profile=self.profile)
            .count(),
            1,
        )

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
        self.assertTrue(cp_a.is_current_version)
        self.assertTrue(cp_b.is_current_version)

    def test_club_player_str(self):
        cp = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo Salah",
        )
        self.assertIn("Mo Salah", str(cp))


class GetOrCreateClubPlayerTests(TestCase):
    """Service: get_or_create_club_player returns/creates the current version."""

    def setUp(self):
        self.club = make_club("Gamma Club", slug="gamma-club")
        self.profile = make_profile("+201033334444")

    def test_creates_first_current_version(self):
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="Player One", player_number=10
        )
        self.assertTrue(created)
        self.assertTrue(cp.is_current_version)
        self.assertIsNone(cp.previous_version_id)
        self.assertEqual(cp.display_name, "Player One")
        self.assertEqual(cp.player_number, 10)

    def test_returns_current_version_even_if_display_name_differs(self):
        existing = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Existing"
        )
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="Different Name"
        )
        self.assertFalse(created)
        self.assertEqual(cp.pk, existing.pk)
        self.assertEqual(cp.display_name, "Existing")

    def test_does_not_create_a_second_version(self):
        get_or_create_club_player(self.club, self.profile, display_name="Old Identity")
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="New Identity"
        )
        self.assertFalse(created)
        self.assertEqual(cp.display_name, "Old Identity")
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            1,
        )


class CreateClubPlayerVersionTests(TestCase):
    """Service: create_club_player_version — the identity-change transition."""

    def setUp(self):
        self.club = make_club("Version Club", slug="version-club")
        self.profile = make_profile("+201033336666")
        self.v1, _ = get_or_create_club_player(
            self.club, self.profile, display_name="Ahmed Ali", player_number=7
        )

    def test_marks_old_historical_and_creates_new_current(self):
        v2 = create_club_player_version(
            self.v1, display_name="Ahmed Salah", player_number=10
        )
        self.v1.refresh_from_db()
        self.assertFalse(self.v1.is_current_version)
        self.assertEqual(self.v1.display_name, "Ahmed Ali")
        self.assertEqual(self.v1.player_number, 7)
        self.assertTrue(v2.is_current_version)
        self.assertEqual(v2.previous_version_id, self.v1.id)
        self.assertEqual(v2.display_name, "Ahmed Salah")
        self.assertEqual(v2.player_number, 10)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            2,
        )

    def test_cannot_version_from_a_historical_row(self):
        create_club_player_version(self.v1, display_name="Ahmed Salah")
        self.v1.refresh_from_db()
        with self.assertRaises(ValueError):
            create_club_player_version(self.v1, display_name="Third")

    def test_same_content_is_idempotent(self):
        again = create_club_player_version(
            self.v1, display_name="Ahmed Ali", player_number=7
        )
        self.assertEqual(again.pk, self.v1.pk)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            1,
        )

    def test_old_versions_are_never_deleted(self):
        v2 = create_club_player_version(self.v1, display_name="Ahmed Salah")
        create_club_player_version(v2, display_name="Captain Ahmed")
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            3,
        )
        self.assertTrue(ClubPlayer.objects.filter(pk=self.v1.pk).exists())


class GetPreferredClubPlayerTests(TestCase):
    """
    Recommended version is last-used from the latest booking, else current.
    """

    def setUp(self):
        self.club = make_club("Preferred Club", slug="preferred-club")
        self.profile = make_profile("+201033335555")
        self.court = make_court(self.club)

    def test_returns_none_when_no_club_player_exists(self):
        self.assertIsNone(get_preferred_club_player(self.club, self.profile))

    def test_falls_back_to_current_version_when_no_bookings(self):
        v1, _ = get_or_create_club_player(
            self.club, self.profile, display_name="Ahmed Ali"
        )
        v2 = create_club_player_version(v1, display_name="Ahmed Salah")
        preferred = get_preferred_club_player(self.club, self.profile)
        self.assertEqual(preferred.pk, v2.pk)
        self.assertIsNone(get_last_used_club_player(self.club, self.profile))

    def test_recommends_club_player_from_latest_booking(self):
        v1, _ = get_or_create_club_player(
            self.club, self.profile, display_name="Ahmed Ali"
        )
        v2 = create_club_player_version(v1, display_name="Ahmed Salah")
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(days=1)
        make_booking(self.club, self.court, v2, created=older)
        make_booking(self.club, self.court, v1, created=newer)
        preferred = get_preferred_club_player(self.club, self.profile)
        self.assertEqual(preferred.pk, v1.pk)
        self.assertEqual(get_last_used_club_player(self.club, self.profile).pk, v1.pk)
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertFalse(v1.is_current_version)
        self.assertTrue(v2.is_current_version)


class ClubPlayerImmutabilityTests(TestCase):
    """
    ClubPlayer identity fields cannot be mutated in place after creation —
    enforced at the model save() layer and the QuerySet.update() layer.
    is_current_version is the only allowed post-creation field.
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

    def test_save_with_update_fields_restricted_to_lifecycle_succeeds(self):
        self.cp.is_current_version = False
        self.cp.save(update_fields=["is_current_version"])
        self.cp.refresh_from_db()
        self.assertFalse(self.cp.is_current_version)

    def test_queryset_update_on_identity_field_raises(self):
        with self.assertRaises(ValueError):
            ClubPlayer.objects.filter(pk=self.cp.pk).update(display_name="Hacked")
        self.cp.refresh_from_db()
        self.assertEqual(self.cp.display_name, "Original Name")

    def test_queryset_update_restricted_to_is_current_version_succeeds(self):
        ClubPlayer.objects.filter(pk=self.cp.pk).update(is_current_version=False)
        self.cp.refresh_from_db()
        self.assertFalse(self.cp.is_current_version)

    def test_queryset_update_of_updated_at_is_rejected(self):
        with self.assertRaises(ValueError):
            ClubPlayer.objects.filter(pk=self.cp.pk).update(updated_at=timezone.now())
