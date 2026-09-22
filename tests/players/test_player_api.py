"""
API tests for ClubPlayer and PlayerProfile endpoints.

Tests cover:
  - ClubPlayer list/create/retrieve (no update/partial_update — see
    test_patch_club_player_is_rejected: ClubPlayer is a permanent,
    versioned/append-only record)
  - Cross-club isolation (scoped queryset returns 404 for wrong club)
  - Role matrix: Admin/Owner/Staff can create
  - Phone find-or-create: same phone across clubs creates ONE PlayerProfile,
    TWO ClubPlayers
  - Versioning: same phone + same display_name in one club reuses the
    existing ClubPlayer; a different display_name records a new version
    (both permanently coexist)
  - PlayerProfile list: only shows profiles linked to the current club
  - PlayerProfile create (find-or-create): 201 for new, 200 for existing
  - Authentication: unauthenticated request returns 401
  - Filter: search by display_name, player_number
"""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.players.models import ClubPlayer, PlayerProfile
from apps.profiles.models import Profile
from tests.profile_factories import grant_admin, grant_owner_club, grant_staff_courts


class PlayerAPITestCase(APITestCase):
    """Shared helpers for player API tests."""

    password = "test-pass-123"

    def create_user(self, phone_number=None, **extra_fields) -> User:
        extra_fields.setdefault("password", getattr(self, "password", "test-pass-123"))
        from tests.profile_factories import create_user as factory_create_user

        return factory_create_user(phone_number=phone_number, **extra_fields)

    def create_club(self, name, slug=None) -> Club:
        return Club.objects.create(
            name=name,
            slug=slug or name.lower().replace(" ", "-"),
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_profile(self, phone, full_name="", user=None) -> PlayerProfile:
        return PlayerProfile.objects.create(
            phone_number=phone,
            full_name=full_name,
            user=user,
            role=Profile.Role.PLAYER,
        )

    def create_club_player(self, club, profile, display_name="", player_number=None):
        return ClubPlayer.objects.create(
            club=club,
            player_profile=profile,
            display_name=display_name,
            player_number=player_number,
        )

    # URL helpers
    def player_list_url(self, club):
        return reverse("club-player-list", kwargs={"club_slug": club.slug})

    def player_detail_url(self, club, pk):
        return reverse("club-player-detail", kwargs={"club_slug": club.slug, "pk": pk})

    def profile_list_url(self, club):
        return reverse("club-player-profile-list", kwargs={"club_slug": club.slug})

    def profile_detail_url(self, club, pk):
        return reverse(
            "club-player-profile-detail", kwargs={"club_slug": club.slug, "pk": pk}
        )


class ClubPlayerCRUDTests(PlayerAPITestCase):
    """ClubPlayer CRUD operations for an Owner."""

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("CRUD Club", slug="crud-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

    def test_create_club_player_returns_201(self):
        url = self.player_list_url(self.club)
        data = {
            "phone_number": "+201011110001",
            "full_name": "Ahmed Hassan",
            "display_name": "Ahmed H",
            "player_number": 10,
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["display_name"], "Ahmed H")
        self.assertEqual(response.data["player_number"], 10)
        self.assertIn("player_profile", response.data)
        self.assertFalse(response.data["player_profile"]["has_account"])

    def test_create_club_player_creates_global_profile(self):
        url = self.player_list_url(self.club)
        self.client.post(
            url,
            {"phone_number": "+201011110002", "full_name": "Global Player"},
            format="json",
        )
        self.assertTrue(
            PlayerProfile.objects.filter(phone_number="+201011110002").exists()
        )

    def test_list_returns_only_current_club_players(self):
        profile_a = self.create_profile("+201011110003")
        self.create_club_player(self.club, profile_a, "Player A")

        other_club = self.create_club("Other Club", slug="other-club")
        other_owner = self.create_user()
        grant_owner_club(other_owner, other_club)
        other_profile = self.create_profile("+201011110004")
        self.create_club_player(other_club, other_profile, "Other Player")

        response = self.client.get(self.player_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        # Only the club's own player should appear.
        self.assertEqual(len(ids), 1)
        self.assertEqual(response.data["results"][0]["display_name"], "Player A")

    def test_retrieve_club_player(self):
        profile = self.create_profile("+201011110005")
        cp = self.create_club_player(self.club, profile, "Retrieve Player")
        response = self.client.get(self.player_detail_url(self.club, cp.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["display_name"], "Retrieve Player")
        self.assertIn("last_used_at", response.data)
        self.assertIsNone(response.data["last_used_at"])
        self.assertNotIn("updated_at", response.data)

    def test_patch_club_player_is_rejected(self):
        """
        ClubPlayer is an append-oriented historical identity record — there is
        no update/partial_update action. PATCH is rejected (403, since the
        permission check runs before method dispatch and denies the
        unrecognized action) and the row is left completely unchanged.
        """
        profile = self.create_profile("+201011110006")
        cp = self.create_club_player(self.club, profile, "Old Name", 5)
        url = self.player_detail_url(self.club, cp.pk)
        response = self.client.patch(
            url, {"display_name": "New Name", "player_number": 7}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        cp.refresh_from_db()
        self.assertEqual(cp.display_name, "Old Name")
        self.assertEqual(cp.player_number, 5)

    def test_unauthenticated_request_returns_401(self):
        self.client.logout()
        self.client.force_authenticate(user=None)
        response = self.client.get(self.player_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ClubPlayerCrossClubIsolationTests(PlayerAPITestCase):
    """Cross-club access must return 404 — no existence leak."""

    def setUp(self):
        self.owner_a = self.create_user()
        self.owner_b = self.create_user()
        self.club_a = self.create_club("Iso Club A", slug="iso-club-a")
        self.club_b = self.create_club("Iso Club B", slug="iso-club-b")
        grant_owner_club(self.owner_a, self.club_a)
        grant_owner_club(self.owner_b, self.club_b)

        self.shared_profile = self.create_profile("+201022220001")
        self.cp_a = self.create_club_player(
            self.club_a, self.shared_profile, "Club A Name"
        )

    def test_owner_b_cannot_retrieve_club_a_player(self):
        self.client.force_authenticate(user=self.owner_b)
        # Owner B uses their own club slug but tries to access Club A's ClubPlayer PK.
        url = self.player_detail_url(self.club_b, self.cp_a.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_is_rejected_regardless_of_club(self):
        """PATCH has no bound action now — rejected before any club-scope check runs."""
        self.client.force_authenticate(user=self.owner_b)
        url = self.player_detail_url(self.club_b, self.cp_a.pk)
        response = self.client.patch(
            url, {"display_name": "Hacked Name"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_club_list_does_not_show_other_club_players(self):
        self.client.force_authenticate(user=self.owner_b)
        response = self.client.get(self.player_list_url(self.club_b))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)


class ClubPlayerRoleTests(PlayerAPITestCase):
    """All roles (Admin, Owner, Staff) can create ClubPlayers."""

    def setUp(self):
        self.club = self.create_club("Role Test Club", slug="role-test-club")
        self.owner = self.create_user()
        self.staff = self.create_user()

        grant_owner_club(self.owner, self.club)
        grant_staff_courts(self.staff, self.club)

    def _create_player(self, user, phone, display_name=""):
        self.client.force_authenticate(user=user)
        return self.client.post(
            self.player_list_url(self.club),
            {"phone_number": phone, "display_name": display_name},
            format="json",
        )

    def test_owner_can_create_club_player(self):
        response = self._create_player(self.owner, "+201033331001", "Owner Player")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_can_create_club_player(self):
        response = self._create_player(self.staff, "+201033331003", "Staff Player")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_list_players_for_unowned_club(self):
        admin_user = self.create_user()
        grant_admin(admin_user)
        # Verify no ownership relationship exists for admin
        self.assertFalse(hasattr(admin_user.profile, "owner_profile"))

        other_club = self.create_club("Admin Other Club", slug="admin-other-club")
        p_target = self.create_profile("+201033332001", full_name="Target Player")
        p_other = self.create_profile("+201033332002", full_name="Other Player")
        cp_target = self.create_club_player(self.club, p_target, "Target Player")
        cp_other = self.create_club_player(other_club, p_other, "Other Player")

        self.client.force_authenticate(user=admin_user)
        response = self.client.get(self.player_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(cp_target.id, ids)
        self.assertNotIn(cp_other.id, ids)
        self.assertEqual(response.data["count"], 1)

    def test_staff_can_list_assigned_club_players_and_is_denied_other_club(self):
        other_club = self.create_club("Staff Other Club", slug="staff-other-club")
        other_owner = self.create_user()
        grant_owner_club(other_owner, other_club)

        p_assigned = self.create_profile("+201033333001", full_name="Assigned Player")
        p_other = self.create_profile("+201033333002", full_name="Other Club Player")
        cp_assigned = self.create_club_player(self.club, p_assigned, "Assigned Player")
        cp_other = self.create_club_player(other_club, p_other, "Other Club Player")

        self.client.force_authenticate(user=self.staff)

        # 1. Staff can list their assigned club players
        response = self.client.get(self.player_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(cp_assigned.id, ids)
        self.assertNotIn(cp_other.id, ids)

        # 2. Staff is denied access to other club's players (403 CLUB_ACCESS_REVOKED)
        other_response = self.client.get(self.player_list_url(other_club))
        self.assertEqual(other_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(other_response.data["success"])
        self.assertEqual(other_response.data["code"], "CLUB_ACCESS_REVOKED")
        self.assertEqual(
            other_response.data.get("details"),
            {"club_slug": other_club.slug},
        )
        self.assertNotIn(str(cp_other.id), str(other_response.data))

    def test_disabled_owner_profile_cannot_list_players(self):
        disabled_owner = self.create_user()
        owner_profile = grant_owner_club(disabled_owner, self.club)
        owner_profile.is_enabled = False
        owner_profile.save(update_fields=["is_enabled"])

        p = self.create_profile("+201033334001", full_name="Secret Player")
        cp = self.create_club_player(self.club, p, "Secret Player")

        self.client.force_authenticate(user=disabled_owner)
        response = self.client.get(self.player_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")
        self.assertEqual(
            response.data.get("details"),
            {"club_slug": self.club.slug},
        )
        self.assertNotIn(str(cp.id), str(response.data))

    def test_disabled_staff_profile_cannot_list_players(self):
        disabled_staff = self.create_user()
        staff_profile = grant_staff_courts(disabled_staff, self.club)
        staff_profile.is_enabled = False
        staff_profile.save(update_fields=["is_enabled"])

        p = self.create_profile("+201033334002", full_name="Secret Staff Player")
        cp = self.create_club_player(self.club, p, "Secret Staff Player")

        self.client.force_authenticate(user=disabled_staff)
        response = self.client.get(self.player_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")
        self.assertEqual(
            response.data.get("details"),
            {"club_slug": self.club.slug},
        )
        self.assertNotIn(str(cp.id), str(response.data))


class ClubPlayerSamePhoneTwoClubsTests(PlayerAPITestCase):
    """Same phone number across two clubs creates one PlayerProfile, two ClubPlayers."""

    def setUp(self):
        self.owner_a = self.create_user()
        self.owner_b = self.create_user()
        self.club_a = self.create_club("Dup Club A", slug="dup-club-a")
        self.club_b = self.create_club("Dup Club B", slug="dup-club-b")
        grant_owner_club(self.owner_a, self.club_a)
        grant_owner_club(self.owner_b, self.club_b)

    def test_same_phone_two_clubs_one_profile_two_club_players(self):
        phone = "+201044440001"

        self.client.force_authenticate(user=self.owner_a)
        resp_a = self.client.post(
            self.player_list_url(self.club_a),
            {"phone_number": phone, "full_name": "Mohamed", "display_name": "Mo"},
            format="json",
        )
        self.assertEqual(resp_a.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=self.owner_b)
        resp_b = self.client.post(
            self.player_list_url(self.club_b),
            {"phone_number": phone, "full_name": "Mohamed", "display_name": "Salah"},
            format="json",
        )
        self.assertEqual(resp_b.status_code, status.HTTP_201_CREATED)

        # Global profile count: exactly 1
        self.assertEqual(PlayerProfile.objects.filter(phone_number=phone).count(), 1)
        # ClubPlayer count: 2 (one per club)
        profile = PlayerProfile.objects.get(phone_number=phone)
        self.assertEqual(ClubPlayer.objects.filter(player_profile=profile).count(), 2)

        # display_names differ per club
        cp_a = ClubPlayer.objects.get(player_profile=profile, club=self.club_a)
        cp_b = ClubPlayer.objects.get(player_profile=profile, club=self.club_b)
        self.assertEqual(cp_a.display_name, "Mo")
        self.assertEqual(cp_b.display_name, "Salah")

        # profile IDs are the same across both responses
        self.assertEqual(
            resp_a.data["player_profile"]["id"],
            resp_b.data["player_profile"]["id"],
        )


class ClubPlayerCreateIdempotentTests(PlayerAPITestCase):
    """
    Creating with the same phone + same display_name/player_number twice in
    one club returns/reuses the existing version. A different display_name
    for the same phone records a brand-new version instead (ClubPlayer is
    versioned/append-only — see apps/players/AGENTS.md "ClubPlayer Lifecycle
    (Versioned, Append-Only)").
    """

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Idem Club", slug="idem-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

    def test_second_create_with_same_content_reuses_existing_club_player(self):
        phone = "+201055550001"
        self.client.post(
            self.player_list_url(self.club),
            {"phone_number": phone, "display_name": "Same Name"},
            format="json",
        )
        response = self.client.post(
            self.player_list_url(self.club),
            {"phone_number": phone, "display_name": "Same Name"},
            format="json",
        )
        # Still 201 — service uses get_or_create
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Only one ClubPlayer row exists — exact same content is reused.
        self.assertEqual(ClubPlayer.objects.filter(club=self.club).count(), 1)

    def test_second_create_with_different_display_name_records_a_new_version(self):
        phone = "+201055550002"
        first_response = self.client.post(
            self.player_list_url(self.club),
            {"phone_number": phone, "display_name": "First"},
            format="json",
        )
        second_response = self.client.post(
            self.player_list_url(self.club),
            {"phone_number": phone, "display_name": "Second"},
            format="json",
        )
        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(first_response.data["id"], second_response.data["id"])
        # Both records permanently exist — nothing was overwritten or removed.
        self.assertEqual(ClubPlayer.objects.filter(club=self.club).count(), 2)


class ClubPlayerFilterTests(PlayerAPITestCase):
    """Search and player_number filters narrow an already-authorized queryset."""

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Filter Club", slug="filter-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

        p1 = self.create_profile("+201066661001", full_name="Khalid Omar")
        p2 = self.create_profile("+201066661002", full_name="Ali Saeed")
        self.cp1 = self.create_club_player(self.club, p1, "Khalid", player_number=10)
        self.cp2 = self.create_club_player(self.club, p2, "Ali", player_number=7)

    def test_search_by_display_name(self):
        url = self.player_list_url(self.club) + "?search=Khalid"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cp1.id, ids)
        self.assertNotIn(self.cp2.id, ids)

    def test_search_by_phone(self):
        url = self.player_list_url(self.club) + "?search=66661002"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cp2.id, ids)
        self.assertNotIn(self.cp1.id, ids)

    def test_filter_by_player_number(self):
        url = self.player_list_url(self.club) + "?player_number=10"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cp1.id, ids)
        self.assertNotIn(self.cp2.id, ids)

    def test_search_by_egyptian_local_phone(self):
        url = self.player_list_url(self.club) + "?search=01066661001"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cp1.id, ids)
        self.assertNotIn(self.cp2.id, ids)

    def test_search_phone_local_and_e164_return_same_player(self):
        url_local = self.player_list_url(self.club) + "?search=01066661001"
        url_e164 = self.player_list_url(self.club) + "?search=%2B201066661001"
        resp_local = self.client.get(url_local)
        resp_e164 = self.client.get(url_e164)
        self.assertEqual(resp_local.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_e164.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_local.data["results"], resp_e164.data["results"])
        self.assertEqual(len(resp_local.data["results"]), 1)
        self.assertEqual(resp_local.data["results"][0]["id"], self.cp1.id)

    def test_search_by_player_profile_full_name(self):
        p = self.create_profile("+201066661003", full_name="Tarek Sayed")
        cp = self.create_club_player(self.club, p, "Abu Sayed")
        url = self.player_list_url(self.club) + "?search=Tarek"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(cp.id, ids)

    def test_search_club_boundary_isolation(self):
        other_club = self.create_club("Other Search Club", slug="other-search-club")
        other_owner = self.create_user()
        grant_owner_club(other_owner, other_club)
        p_other = self.create_profile("+201066661004", full_name="Khalid Foreign")
        cp_other = self.create_club_player(other_club, p_other, "Khalid Other")

        url = self.player_list_url(self.club) + "?search=Khalid"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cp1.id, ids)
        self.assertNotIn(cp_other.id, ids)

    def test_search_includes_both_with_and_without_account(self):
        user = self.create_user()
        p_with_account = self.create_profile(
            "+201066661005", full_name="Account User", user=user
        )
        p_no_account = self.create_profile("+201066661006", full_name="No Account User")
        cp_account = self.create_club_player(
            self.club, p_with_account, "Player Account"
        )
        cp_no_account = self.create_club_player(
            self.club, p_no_account, "Player No Account"
        )

        url = self.player_list_url(self.club) + "?search=Player"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        account_item = next(r for r in results if r["id"] == cp_account.id)
        no_account_item = next(r for r in results if r["id"] == cp_no_account.id)
        self.assertTrue(account_item["player_profile"]["has_account"])
        self.assertFalse(no_account_item["player_profile"]["has_account"])


class ClubPlayerOrderingTests(PlayerAPITestCase):
    """
    Tests for ordering ClubPlayer list results by last_used_at and created.
    """

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Ordering Club", slug="ordering-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

        self.court = Court.objects.create(
            club=self.club,
            name="Ordering Court",
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )

        self.p_new = self.create_profile("+201012345678", full_name="Newest Used")
        self.cp_new = self.create_club_player(
            self.club, self.p_new, "Ahmed H.", player_number=15
        )

        self.p_old = self.create_profile("+201012345679", full_name="Oldest Used")
        self.cp_old = self.create_club_player(
            self.club, self.p_old, "Ahmed Hassan", player_number=11
        )

        self.p_unused = self.create_profile("+201012345680", full_name="Never Used")
        self.cp_unused = self.create_club_player(
            self.club, self.p_unused, "Ahmed Unused", player_number=20
        )

        self.time_old = timezone.now() - timedelta(days=10)
        self.time_new = timezone.now() - timedelta(days=1)

        b_old = Booking.objects.create(
            club=self.club,
            court=self.court,
            club_player=self.cp_old,
            start_time=self.time_old,
            end_time=self.time_old + timedelta(hours=1),
            total_price=Decimal("300.00"),
        )
        Booking.objects.filter(pk=b_old.pk).update(created=self.time_old)

        b_new_earlier = Booking.objects.create(
            club=self.club,
            court=self.court,
            club_player=self.cp_new,
            start_time=self.time_old + timedelta(days=1),
            end_time=self.time_old + timedelta(days=1, hours=1),
            total_price=Decimal("300.00"),
        )
        Booking.objects.filter(pk=b_new_earlier.pk).update(
            created=self.time_old + timedelta(days=1)
        )

        b_new_latest = Booking.objects.create(
            club=self.club,
            court=self.court,
            club_player=self.cp_new,
            start_time=self.time_new,
            end_time=self.time_new + timedelta(hours=1),
            total_price=Decimal("300.00"),
        )
        Booking.objects.filter(pk=b_new_latest.pk).update(created=self.time_new)

    def test_last_used_at_is_latest_booking_for_that_club_player(self):
        """1. last_used_at is the latest Booking for that ClubPlayer."""
        response = self.client.get(self.player_detail_url(self.club, self.cp_new.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["last_used_at"])
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(response.data["last_used_at"])
        self.assertEqual(parsed, self.time_new)

    def test_order_by_desc_last_used_at_returns_newest_used_first(self):
        """2. order_by=-last_used_at returns newest-used first."""
        url = self.player_list_url(self.club) + "?order_by=-last_used_at"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        ids = [r["id"] for r in results]
        self.assertEqual(ids[:2], [self.cp_new.id, self.cp_old.id])

    def test_default_ordering_is_desc_last_used_at_with_nulls_last(self):
        """Without order_by param, defaults to newest-used first with nulls last."""
        url = self.player_list_url(self.club)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        ids = [r["id"] for r in results]
        self.assertEqual(ids[:2], [self.cp_new.id, self.cp_old.id])
        self.assertIn(self.cp_unused.id, ids)
        unused_index = ids.index(self.cp_unused.id)
        self.assertGreater(unused_index, ids.index(self.cp_new.id))
        self.assertGreater(unused_index, ids.index(self.cp_old.id))

    def test_order_by_asc_last_used_at_returns_oldest_used_first(self):
        """3. order_by=last_used_at works."""
        url = self.player_list_url(self.club) + "?order_by=last_used_at"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        ids = [r["id"] for r in results]
        self.assertEqual(ids[:2], [self.cp_old.id, self.cp_new.id])

    def test_never_used_players_returned_and_sorted_after_used_players_desc(self):
        """4. Never-used ClubPlayers sort after used players when descending."""
        url = self.player_list_url(self.club) + "?order_by=-last_used_at"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        ids = [r["id"] for r in results]
        self.assertIn(self.cp_unused.id, ids)
        unused_index = ids.index(self.cp_unused.id)
        self.assertGreater(unused_index, ids.index(self.cp_new.id))
        self.assertGreater(unused_index, ids.index(self.cp_old.id))
        unused_item = next(r for r in results if r["id"] == self.cp_unused.id)
        self.assertIsNone(unused_item["last_used_at"])

    def test_booking_belonging_to_another_club_player_does_not_affect_value(self):
        """5. A Booking belonging to another ClubPlayer does not affect the value."""
        response = self.client.get(self.player_detail_url(self.club, self.cp_unused.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["last_used_at"])

        other_club = self.create_club("Other Ordering Club", slug="other-ordering-club")
        other_court = Court.objects.create(
            club=other_club,
            name="Other Court",
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )
        Booking.objects.create(
            club=other_club,
            court=other_court,
            club_player=self.cp_unused,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=1),
            total_price=Decimal("300.00"),
        )
        response = self.client.get(self.player_detail_url(self.club, self.cp_unused.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["last_used_at"])

    def test_search_by_phone_and_order_by_last_used_at_together(self):
        """6. Search by phone + order_by=-last_used_at works together."""
        url = (
            self.player_list_url(self.club)
            + "?search=0101234567&order_by=-last_used_at"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(ids, [self.cp_new.id, self.cp_old.id])
        self.assertNotIn(self.cp_unused.id, ids)

        url_exact = (
            self.player_list_url(self.club)
            + "?search=01012345678&order_by=-last_used_at"
        )
        resp_exact = self.client.get(url_exact)
        self.assertEqual(resp_exact.status_code, status.HTTP_200_OK)
        ids_exact = [r["id"] for r in resp_exact.data["results"]]
        self.assertEqual(ids_exact, [self.cp_new.id])
        self.assertEqual(
            resp_exact.data["results"][0]["last_used_at"],
            response.data["results"][0]["last_used_at"],
        )

    def test_order_by_created_and_desc_created(self):
        """Order by created and -created works."""
        url_asc = self.player_list_url(self.club) + "?order_by=created"
        resp_asc = self.client.get(url_asc)
        self.assertEqual(resp_asc.status_code, status.HTTP_200_OK)
        ids_asc = [r["id"] for r in resp_asc.data["results"]]
        self.assertEqual(ids_asc, [self.cp_new.id, self.cp_old.id, self.cp_unused.id])

        url_desc = self.player_list_url(self.club) + "?order_by=-created"
        resp_desc = self.client.get(url_desc)
        self.assertEqual(resp_desc.status_code, status.HTTP_200_OK)
        ids_desc = [r["id"] for r in resp_desc.data["results"]]
        self.assertEqual(ids_desc, [self.cp_unused.id, self.cp_old.id, self.cp_new.id])

    def test_unallowed_ordering_fields_fall_back_to_default(self):
        """Arbitrary fields in order_by are ignored and do not cause errors."""
        url = self.player_list_url(self.club) + "?order_by=player_number"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(ids, sorted(ids))


class ClubPlayerPaginationTests(PlayerAPITestCase):
    """ClubPlayer list pagination obeys standard DRF PageNumberPagination."""

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Page Club", slug="page-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

    def test_pagination_over_20_records(self):
        created_cps = []
        for i in range(1, 26):
            profile = self.create_profile(f"+20108888{i:04d}", full_name=f"Player {i}")
            cp = self.create_club_player(
                self.club, profile, display_name=f"Player {i}", player_number=i
            )
            created_cps.append(cp)

        # Page 1
        url = self.player_list_url(self.club)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 25)
        self.assertIsNone(response.data["previous"])
        self.assertIsNotNone(response.data["next"])
        self.assertIn("page=2", response.data["next"])
        self.assertEqual(len(response.data["results"]), 20)

        page_1_ids = [r["id"] for r in response.data["results"]]

        # Page 2
        response_p2 = self.client.get(response.data["next"])
        self.assertEqual(response_p2.status_code, status.HTTP_200_OK)
        self.assertEqual(response_p2.data["count"], 25)
        self.assertIsNotNone(response_p2.data["previous"])
        self.assertIsNone(response_p2.data["next"])
        self.assertEqual(len(response_p2.data["results"]), 5)

        page_2_ids = [r["id"] for r in response_p2.data["results"]]

        all_ids = set(page_1_ids + page_2_ids)
        expected_ids = {cp.id for cp in created_cps}
        self.assertEqual(all_ids, expected_ids)
        self.assertEqual(len(page_1_ids) + len(page_2_ids), 25)
        self.assertTrue(set(page_1_ids).isdisjoint(set(page_2_ids)))


class PlayerProfileViewSetTests(PlayerAPITestCase):
    """PlayerProfile find-or-create and club-linked list."""

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Profile Club", slug="profile-club")
        grant_owner_club(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

    def test_create_new_profile_returns_201(self):
        url = self.profile_list_url(self.club)
        response = self.client.post(
            url,
            {"phone_number": "+201077770001", "full_name": "New Player"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["full_name"], "New Player")

    def test_create_existing_profile_returns_200(self):
        self.create_profile("+201077770002", full_name="Existing")
        url = self.profile_list_url(self.club)
        response = self.client.post(
            url, {"phone_number": "+201077770002"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Existing")

    def test_list_returns_only_club_linked_profiles(self):
        # Profile registered in club via ClubPlayer
        p1 = self.create_profile("+201077770003")
        self.create_club_player(self.club, p1, "In Club")

        # Profile that exists globally but NOT in this club
        _p2 = self.create_profile("+201077770004")  # no ClubPlayer for this club

        url = self.profile_list_url(self.club)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(p1.id, ids)
        self.assertNotIn(_p2.id, ids)

    def test_retrieve_profile(self):
        p = self.create_profile("+201077770005", full_name="Retrieve Me")
        self.create_club_player(self.club, p)
        response = self.client.get(self.profile_detail_url(self.club, p.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Retrieve Me")
        self.assertFalse(response.data["has_account"])

    def test_retrieve_includes_club_player_versions_and_recommended_id(self):
        p = self.create_profile("+201077770007", full_name="Ahmed")
        v1 = self.create_club_player(self.club, p, "Ahmed Ali", player_number=7)
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=p,
            display_name="Ahmed Salah",
            player_number=10,
        )
        response = self.client.get(self.profile_detail_url(self.club, p.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        version_ids = [row["id"] for row in response.data["club_player_versions"]]
        self.assertEqual(set(version_ids), {v1.id, v2.id})
        self.assertEqual(response.data["recommended_club_player_id"], v2.id)
        for row in response.data["club_player_versions"]:
            self.assertNotIn("last_used_at", row)
            self.assertNotIn("updated_at", row)

    def test_recommended_id_is_club_player_from_latest_booking(self):
        p = self.create_profile("+201077770008", full_name="Ahmed")
        v1 = self.create_club_player(self.club, p, "Ahmed Ali", player_number=7)
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=p,
            display_name="Ahmed Salah",
            player_number=10,
        )
        court = Court.objects.create(
            club=self.club,
            name="Profile Court",
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )
        start = timezone.now()
        booking = Booking.objects.create(
            club=self.club,
            court=court,
            club_player=v1,
            start_time=start,
            end_time=start + timedelta(hours=1),
            total_price=Decimal("300.00"),
        )
        Booking.objects.filter(pk=booking.pk).update(
            created=timezone.now() - timedelta(hours=1)
        )
        response = self.client.get(self.profile_detail_url(self.club, p.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["recommended_club_player_id"], v1.id)
        self.assertNotEqual(response.data["recommended_club_player_id"], v2.id)

    def test_has_account_true_when_user_linked(self):
        u = self.create_user()
        p = self.create_profile("+201077770006", user=u)
        self.create_club_player(self.club, p)
        response = self.client.get(self.profile_detail_url(self.club, p.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["has_account"])

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.profile_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
