"""Compatibility tests for the retired membership-presence heartbeat."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.profiles.models import Profile, StaffProfile


class SyncHeartbeatAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="heartbeat-user", password="test-pass-123"
        )
        self.club = Club.objects.create(
            name="Heartbeat Club",
            slug="heartbeat-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court = Court.objects.create(
            club=self.club, name="Heartbeat Court", default_price="250.00"
        )
        profile = Profile.objects.create(user=self.user, role=Profile.Role.STAFF)
        self.staff_profile = StaffProfile.objects.create(
            profile=profile, court=self.court
        )

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(reverse("sync-heartbeat"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_request_is_a_compatibility_acknowledgement(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse("sync-heartbeat"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("last_sync_at", response.data)

    def test_client_timestamp_does_not_become_profile_state(self):
        self.client.force_authenticate(user=self.user)
        modified = self.user.profile.modified
        response = self.client.post(
            reverse("sync-heartbeat"),
            {"last_sync_at": "2000-01-01T00:00:00Z"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.modified, modified)

    def test_normal_authorized_traffic_does_not_mutate_profile_state(self):
        self.client.force_authenticate(user=self.user)
        modified = self.user.profile.modified
        response = self.client.get(
            reverse("club-court-list", kwargs={"club_slug": self.club.slug})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.modified, modified)

    def test_removing_staff_scope_revokes_club_access(self):
        self.staff_profile.delete()
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            reverse("club-court-list", kwargs={"club_slug": self.club.slug})
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")
