"""
API tests for ClubPlayer and PlayerProfile endpoints.

Tests cover:
  - ClubPlayer list/create/retrieve (no update/partial_update — see
    test_patch_club_player_is_rejected: ClubPlayer is a permanent,
    versioned/append-only record)
  - Cross-club isolation (scoped queryset returns 404 for wrong club)
  - Role matrix: Owner/Manager/Staff can create
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
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.players.models import ClubPlayer, PlayerProfile


class PlayerAPITestCase(APITestCase):
    """Shared helpers for player API tests."""

    password = "test-pass-123"

    def create_user(self, username, **extra_fields) -> User:
        return User.objects.create_user(
            username=username, password=self.password, **extra_fields
        )

    def create_club(self, name, slug=None) -> Club:
        return Club.objects.create(
            name=name,
            slug=slug or name.lower().replace(" ", "-"),
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_membership(self, user, club, role, **kwargs) -> ClubMembership:
        return ClubMembership.objects.create(club=club, user=user, role=role, **kwargs)

    def create_profile(self, phone, full_name="", user=None) -> PlayerProfile:
        return PlayerProfile.objects.create(
            phone_number=phone, full_name=full_name, user=user
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
        self.owner = self.create_user("crud-owner")
        self.club = self.create_club("CRUD Club", slug="crud-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
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
        self.assertTrue(response.data["is_current_version"])
        self.assertIsNone(response.data["previous_version"])
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
        other_owner = self.create_user("other-owner")
        self.create_membership(other_owner, other_club, ClubMembership.Role.OWNER)
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
        self.assertNotIn("last_used_at", response.data)
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
        self.owner_a = self.create_user("iso-owner-a")
        self.owner_b = self.create_user("iso-owner-b")
        self.club_a = self.create_club("Iso Club A", slug="iso-club-a")
        self.club_b = self.create_club("Iso Club B", slug="iso-club-b")
        self.create_membership(self.owner_a, self.club_a, ClubMembership.Role.OWNER)
        self.create_membership(self.owner_b, self.club_b, ClubMembership.Role.OWNER)

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
    """All roles (Owner, Manager, Staff) can create ClubPlayers."""

    def setUp(self):
        self.club = self.create_club("Role Test Club", slug="role-test-club")
        # Staff is not assigned to a specific court for these ClubPlayer tests.

        self.owner = self.create_user("role-owner")
        self.manager = self.create_user("role-manager")
        self.staff = self.create_user("role-staff")

        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(self.staff, self.club, ClubMembership.Role.STAFF)

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

    def test_manager_can_create_club_player(self):
        response = self._create_player(self.manager, "+201033331002", "Manager Player")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_can_create_club_player(self):
        response = self._create_player(self.staff, "+201033331003", "Staff Player")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class ClubPlayerSamePhoneTwoClubsTests(PlayerAPITestCase):
    """Same phone number across two clubs creates one PlayerProfile, two ClubPlayers."""

    def setUp(self):
        self.owner_a = self.create_user("dup-owner-a")
        self.owner_b = self.create_user("dup-owner-b")
        self.club_a = self.create_club("Dup Club A", slug="dup-club-a")
        self.club_b = self.create_club("Dup Club B", slug="dup-club-b")
        self.create_membership(self.owner_a, self.club_a, ClubMembership.Role.OWNER)
        self.create_membership(self.owner_b, self.club_b, ClubMembership.Role.OWNER)

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
        self.owner = self.create_user("idem-owner")
        self.club = self.create_club("Idem Club", slug="idem-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
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
        self.assertFalse(
            ClubPlayer.objects.get(pk=first_response.data["id"]).is_current_version
        )
        self.assertTrue(second_response.data["is_current_version"])
        self.assertEqual(
            second_response.data["previous_version"], first_response.data["id"]
        )
        # Both versions permanently exist — nothing was overwritten or removed.
        self.assertEqual(ClubPlayer.objects.filter(club=self.club).count(), 2)


class ClubPlayerFilterTests(PlayerAPITestCase):
    """Search and player_number filters narrow an already-authorized queryset."""

    def setUp(self):
        self.owner = self.create_user("filter-owner")
        self.club = self.create_club("Filter Club", slug="filter-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
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

    def test_filter_current_versions(self):
        url = self.player_list_url(self.club) + "?is_current_version=true"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(r["is_current_version"] for r in response.data["results"]))


class PlayerProfileViewSetTests(PlayerAPITestCase):
    """PlayerProfile find-or-create and club-linked list."""

    def setUp(self):
        self.owner = self.create_user("pp-owner")
        self.club = self.create_club("Profile Club", slug="profile-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
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
        v1.is_current_version = False
        v1.save(update_fields=["is_current_version"])
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=p,
            display_name="Ahmed Salah",
            player_number=10,
            previous_version=v1,
            is_current_version=True,
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
        v1.is_current_version = False
        v1.save(update_fields=["is_current_version"])
        v2 = ClubPlayer.objects.create(
            club=self.club,
            player_profile=p,
            display_name="Ahmed Salah",
            player_number=10,
            previous_version=v1,
            is_current_version=True,
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
            customer_name="Ahmed Ali",
            customer_phone="+201077770008",
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
        u = self.create_user("pp-linked-user")
        p = self.create_profile("+201077770006", user=u)
        self.create_club_player(self.club, p)
        response = self.client.get(self.profile_detail_url(self.club, p.pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["has_account"])

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.profile_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
