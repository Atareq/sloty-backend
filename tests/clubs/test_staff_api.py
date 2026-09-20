"""Tests for Club Staff Management API endpoints (/api/v1/clubs/{club_slug}/staff/)."""

from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.profiles.models import Profile, StaffCourtAccess, StaffProfile
from tests.profile_factories import (
    create_admin_user,
    create_owner_user,
    create_staff_user,
)


class ClubStaffAPITests(APITestCase):
    def setUp(self):
        self.club = Club.objects.create(
            name="Cairo Sports Club",
            slug="cairo-sports",
            governorate="CAIRO",
            city="NASR_CITY",
        )
        self.other_club = Club.objects.create(
            name="Alex Sports Club",
            slug="alex-sports",
            governorate="ALEXANDRIA",
            city="ALEXANDRIA",
        )
        self.court_1 = Court.objects.create(
            club=self.club, name="Court 1", default_price="200.00"
        )
        self.court_2 = Court.objects.create(
            club=self.club, name="Court 2", default_price="250.00"
        )
        self.other_court = Court.objects.create(
            club=self.other_club, name="Other Court", default_price="300.00"
        )

        self.owner = create_owner_user("cairo-owner", clubs=[self.club])
        self.other_owner = create_owner_user("alex-owner", clubs=[self.other_club])
        self.admin = create_admin_user("super-admin")
        self.staff_user = create_staff_user(
            "cairo-staff", club=self.club, courts=[self.court_1]
        )

    def test_owner_can_list_staff_for_their_club(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["role"], "STAFF")
        self.assertEqual(item["username"], "cairo-staff")
        self.assertEqual(len(item["courts"]), 1)
        self.assertEqual(item["courts"][0]["id"], self.court_1.id)

    def test_owner_can_create_staff_with_initial_courts(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "username": "new-staff-member",
            "password": "SecurePassword123!",
            "first_name": "Omar",
            "last_name": "Ali",
            "phone_number": "+201011112222",
            "court_ids": [self.court_1.id, self.court_2.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "STAFF")
        self.assertEqual(response.data["username"], "new-staff-member")
        self.assertEqual(response.data["first_name"], "Omar")
        self.assertEqual(len(response.data["courts"]), 2)

        # Verify DB state
        created_user = User.objects.get(username="new-staff-member")
        self.assertTrue(created_user.check_password("SecurePassword123!"))
        profile = created_user.profile
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, Profile.Role.STAFF)
        self.assertEqual(profile.created_by_id, self.owner.profile.pk)
        self.assertEqual(profile.staff_profile.club, self.club)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 2)

    def test_create_staff_rejects_cross_club_court_assignment(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "username": "cross-club-staff",
            "password": "SecurePassword123!",
            "phone_number": "+201033334444",
            "court_ids": [self.court_1.id, self.other_court.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="cross-club-staff").exists())
        self.assertFalse(
            StaffProfile.objects.filter(user__username="cross-club-staff").exists()
        )
        self.assertFalse(
            StaffCourtAccess.objects.filter(
                staff_profile__user__username="cross-club-staff"
            ).exists()
        )

    def test_admin_cannot_directly_create_staff(self):
        self.client.force_authenticate(user=self.admin)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "username": "admin-created-staff",
            "password": "SecurePassword123!",
            "phone_number": "+201099998888",
            "court_ids": [self.court_1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="admin-created-staff").exists())

    def test_owner_cannot_create_staff_in_unowned_club(self):
        self.client.force_authenticate(user=self.other_owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "username": "foreign-staff",
            "password": "SecurePassword123!",
            "phone_number": "+201099997777",
            "court_ids": [self.court_1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="foreign-staff").exists())

    def test_owner_can_retrieve_staff_detail(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["profile_id"], profile_id)
        self.assertEqual(response.data["username"], "cairo-staff")

    def test_owner_can_patch_staff_details_and_toggle_is_enabled(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/"
        payload = {
            "first_name": "UpdatedName",
            "is_enabled": False,
        }
        response = self.client.patch(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first_name"], "UpdatedName")
        self.assertFalse(response.data["is_enabled"])

        profile.refresh_from_db()
        self.assertFalse(profile.is_enabled)
        self.staff_user.refresh_from_db()
        self.assertEqual(self.staff_user.first_name, "UpdatedName")

    def test_owner_can_replace_assigned_courts(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/courts/"

        # Assign both court 1 and court 2
        response = self.client.put(
            url,
            {"court_ids": [self.court_1.id, self.court_2.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["courts"]), 2)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 2)

        # Clear to 0 courts
        response = self.client.put(url, {"court_ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["courts"]), 0)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 0)

    def test_replace_courts_rejects_foreign_club_court(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/courts/"

        response = self.client.put(
            url,
            {"court_ids": [self.other_court.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_delete_staff_profile(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Profile.objects.filter(id=profile_id).exists())
        self.assertFalse(StaffProfile.objects.filter(pk=profile_id).exists())

    def test_staff_role_cannot_access_staff_management_api(self):
        self.client.force_authenticate(user=self.staff_user)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_club_owner_denied_access(self):
        self.client.force_authenticate(user=self.other_owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_admin_can_manage_staff_across_clubs(self):
        self.client.force_authenticate(user=self.admin)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
