"""Tests for Owner onboarding API (/api/v1/owners/)."""

from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.profiles.models import OwnerProfile, Profile
from tests.profile_factories import (
    create_admin_user,
    create_owner_user,
    create_staff_user,
)


class OwnerOnboardingAPITests(APITestCase):
    def setUp(self):
        self.club1 = Club.objects.create(
            name="Alpha Sports Club",
            slug="alpha-sports",
            governorate="CAIRO",
            city="NASR_CITY",
        )
        self.club2 = Club.objects.create(
            name="Beta Sports Club",
            slug="beta-sports",
            governorate="CAIRO",
            city="MAADI",
        )

        self.admin_user = create_admin_user("platform-admin")
        self.admin_profile = self.admin_user.profile.admin_profile

        self.owner_user = create_owner_user("existing-owner", clubs=[self.club1])
        self.owner_profile = self.owner_user.profile.owner_profile

        self.staff_user = create_staff_user("existing-staff", club=self.club1)
        self.staff_profile = self.staff_user.profile.staff_profile

    def test_admin_can_create_owner_atomically(self):
        self.client.force_authenticate(user=self.admin_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "new-club-owner",
            "password": "SecurePassword123!",
            "first_name": "Tarek",
            "last_name": "Owner",
            "email": "tarek.owner@example.com",
            "phone_number": "+201011112222",
            "club_ids": [self.club1.id, self.club2.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data
        self.assertEqual(data["role"], Profile.Role.OWNER)
        self.assertEqual(data["username"], "new-club-owner")
        self.assertEqual(data["first_name"], "Tarek")
        self.assertEqual(data["last_name"], "Owner")
        self.assertEqual(data["created_by"], self.admin_profile.pk)
        self.assertEqual(set(data["club_ids"]), {self.club1.id, self.club2.id})

        # Verify DB records
        created_user = User.objects.get(username="new-club-owner")
        self.assertTrue(created_user.check_password("SecurePassword123!"))
        profile = created_user.profile
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, Profile.Role.OWNER)
        self.assertEqual(profile.created_by_id, self.admin_profile.pk)

        owner_profile = profile.owner_profile
        self.assertEqual(
            set(owner_profile.clubs.values_list("id", flat=True)),
            {self.club1.id, self.club2.id},
        )
        self.assertIn(
            owner_profile.pk,
            self.admin_profile.created_profiles.values_list("pk", flat=True),
        )

    def test_owner_cannot_create_owner(self):
        self.client.force_authenticate(user=self.owner_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "sub-owner",
            "password": "Password123!",
            "club_ids": [self.club1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="sub-owner").exists())

    def test_staff_cannot_create_owner(self):
        self.client.force_authenticate(user=self.staff_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "staff-promoted-owner",
            "password": "Password123!",
            "club_ids": [self.club1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="staff-promoted-owner").exists())

    def test_disabled_admin_cannot_create_owner(self):
        self.admin_user.profile.is_enabled = False
        self.admin_user.profile.save()

        self.client.force_authenticate(user=self.admin_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "blocked-owner",
            "password": "Password123!",
            "club_ids": [self.club1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(username="blocked-owner").exists())

    def test_unauthenticated_user_cannot_create_owner(self):
        url = "/api/v1/owners/"
        payload = {
            "username": "anon-owner",
            "password": "Password123!",
            "club_ids": [self.club1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(username="anon-owner").exists())

    def test_client_cannot_override_role_or_created_by(self):
        self.client.force_authenticate(user=self.admin_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "spoofed-owner",
            "password": "Password123!",
            "role": "ADMIN",
            "created_by": 9999,
            "club_ids": [self.club1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_user = User.objects.get(username="spoofed-owner")
        self.assertEqual(created_user.profile.role, Profile.Role.OWNER)
        self.assertEqual(created_user.profile.created_by_id, self.admin_profile.pk)

    def test_invalid_club_assignment_rolls_back_atomically(self):
        self.client.force_authenticate(user=self.admin_user)
        url = "/api/v1/owners/"
        payload = {
            "username": "rollback-owner",
            "password": "Password123!",
            "club_ids": [self.club1.id, 99999],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Atomicity check: no User or OwnerProfile created
        self.assertFalse(User.objects.filter(username="rollback-owner").exists())
        self.assertFalse(
            OwnerProfile.objects.filter(user__username="rollback-owner").exists()
        )

    def test_admin_can_list_and_retrieve_owners(self):
        self.client.force_authenticate(user=self.admin_user)

        # List
        response = self.client.get("/api/v1/owners/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertTrue(len(results) >= 1)
        self.assertIn(self.owner_profile.pk, [r["profile_id"] for r in results])

        # Retrieve
        detail_url = f"/api/v1/owners/{self.owner_profile.pk}/"
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["profile_id"], self.owner_profile.pk)
        self.assertEqual(response.data["username"], "existing-owner")
