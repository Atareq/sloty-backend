"""
Unit tests for PlayerProfile and ClubPlayer models.

Tests cover:
  - Phone uniqueness (global identity constraint)
  - Nullable user FK (player without an account)
  - User linking via service
  - Cross-club independence (same profile, different club-local metadata)
  - Service layer: find_or_create, get_or_create_club_player,
    get_current_club_player, get_preferred_club_player,
    get_last_used_club_player, link_player_to_user conflict
  - Latest Booking identity as source of truth
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.services import (
    find_or_create_player_profile,
    get_current_club_player,
    get_last_used_club_player,
    get_or_create_club_player,
    get_preferred_club_player,
    link_player_to_user,
)
from apps.profiles.models import Profile


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
        phone_number=phone,
        full_name=full_name,
        user=user,
        role=Profile.Role.PLAYER,
    )


def make_court(club, name="Court 1"):
    return Court.objects.create(
        club=club,
        name=name,
        default_price=Decimal("300.00"),
        slot_duration_minutes=60,
    )


def make_booking(club, court, club_player, *, created_at=None):
    start = timezone.now()
    booking = Booking.objects.create(
        club=club,
        court=court,
        club_player=club_player,
        start_time=start,
        end_time=start + timedelta(hours=1),
        total_price=Decimal("300.00"),
    )
    if created_at is not None:
        Booking.objects.filter(pk=booking.pk).update(created_at=created_at)
        booking.refresh_from_db()
    return booking


class PlayerProfilePhoneUniquenessTests(TestCase):
    """Phone number is the global identity key — must be unique."""

    def test_duplicate_phone_is_rejected_by_validation(self):
        make_profile("+201012345678", full_name="Ahmed")
        with self.assertRaises(ValidationError):
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

    def test_player_profile_is_real_profile_child(self):
        self.assertTrue(issubclass(PlayerProfile, Profile))
        link = PlayerProfile._meta.parents[Profile]
        self.assertTrue(link.remote_field.parent_link)
        self.assertTrue(link.primary_key)
        self.assertEqual(link.name, "profile_ptr")
        self.assertNotIn(
            "role", {field.name for field in PlayerProfile._meta.local_fields}
        )
        self.assertNotIn(
            "user", {field.name for field in PlayerProfile._meta.local_fields}
        )
        self.assertIs(PlayerProfile.clean, Profile.clean)
        self.assertIs(PlayerProfile.save, Profile.save)

    def test_creating_user_does_not_create_player_profile_or_club_player(self):
        user = make_user("account-without-player-profile")

        self.assertFalse(PlayerProfile.objects.filter(user=user).exists())
        self.assertFalse(ClubPlayer.objects.filter(player_profile__user=user).exists())

    def test_profile_without_user_is_valid(self):
        p = make_profile("+201012345678")
        self.assertEqual(p.role, Profile.Role.PLAYER)
        self.assertIsNone(p.user)
        self.assertIsNone(p.user_id)
        self.assertFalse(p.verified)
        self.assertTrue(Profile.objects.filter(pk=p.pk).exists())

    def test_profile_with_user_is_valid(self):
        u = make_user("player-user")
        p = make_profile("+201012345678", user=u)
        self.assertEqual(p.user_id, u.pk)
        self.assertEqual(u.role, Profile.Role.PLAYER)

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
    """ClubPlayer rows and multi-club independence."""

    def setUp(self):
        self.club = make_club("Alpha Club", slug="alpha-club")
        self.profile = make_profile("+201022223333")

    def test_multiple_club_players_can_coexist_for_same_pair(self):
        v1 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo",
        )
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mohamed",
        )
        self.assertNotEqual(v1.pk, v2.pk)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club, player_profile=self.profile
            ).count(),
            2,
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

    def test_club_player_str(self):
        cp = ClubPlayer.objects.create(
            club=self.club,
            player_profile=self.profile,
            display_name="Mo Salah",
        )
        self.assertIn("Mo Salah", str(cp))


class GetOrCreateClubPlayerTests(TestCase):
    """Service: get_or_create_club_player returns/creates current identity."""

    def setUp(self):
        self.club = make_club("Gamma Club", slug="gamma-club")
        self.profile = make_profile("+201033334444")

    def test_creates_first_club_player(self):
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="Player One", player_number=10
        )
        self.assertTrue(created)
        self.assertEqual(cp.display_name, "Player One")
        self.assertEqual(cp.player_number, 10)

    def test_returns_current_even_if_display_name_differs(self):
        existing = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Existing"
        )
        cp, created = get_or_create_club_player(
            self.club, self.profile, display_name="Different Name"
        )
        self.assertFalse(created)
        self.assertEqual(cp.pk, existing.pk)
        self.assertEqual(cp.display_name, "Existing")

    def test_does_not_create_a_second_row(self):
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

    def test_falls_back_to_latest_club_player_when_no_bookings(self):
        v1, _ = get_or_create_club_player(
            self.club, self.profile, display_name="Ahmed Ali"
        )
        v2 = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Ahmed Salah"
        )
        preferred = get_preferred_club_player(self.club, self.profile)
        self.assertEqual(preferred.pk, v2.pk)
        self.assertIsNone(get_last_used_club_player(self.club, self.profile))

    def test_recommends_club_player_from_latest_booking(self):
        v1, _ = get_or_create_club_player(
            self.club, self.profile, display_name="Ahmed Ali"
        )
        v2 = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Ahmed Salah"
        )
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(days=1)
        make_booking(self.club, self.court, v2, created_at=older)
        make_booking(self.club, self.court, v1, created_at=newer)
        preferred = get_preferred_club_player(self.club, self.profile)
        self.assertEqual(preferred.pk, v1.pk)
        self.assertEqual(get_last_used_club_player(self.club, self.profile).pk, v1.pk)


class LatestBookingIdentityTests(TestCase):
    """The latest Booking.club_player is the source of truth for identity."""

    def setUp(self):
        self.club = make_club("Latest Booking Club", slug="latest-booking-club")
        self.profile = make_profile("+201099991111")
        self.court = make_court(self.club)

    def test_fallback_to_latest_club_player_when_no_booking(self):
        ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="First"
        )
        cp2 = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Second"
        )
        self.assertEqual(get_current_club_player(self.club, self.profile).pk, cp2.pk)

    def test_latest_booking_determines_current_identity(self):
        cp1 = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="First"
        )
        cp2 = ClubPlayer.objects.create(
            club=self.club, player_profile=self.profile, display_name="Second"
        )
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(days=1)
        make_booking(self.club, self.court, cp2, created_at=older)
        make_booking(self.club, self.court, cp1, created_at=newer)
        # Even though cp2 has a higher id, cp1 is on the latest booking!
        self.assertEqual(get_current_club_player(self.club, self.profile).pk, cp1.pk)
