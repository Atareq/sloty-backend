"""
Tests for the explicit offline/PWA sync heartbeat endpoint.

ARCHITECTURAL INVARIANT:
POST /api/v1/me/sync-heartbeat/ is the ONLY mechanism that updates
ClubMembership.last_sync_at. Ordinary authenticated API traffic (e.g. the
Courts endpoints, now migrated to the Authorization Spine) must never update
it as an implicit response side effect - presence/sync tracking is
intentionally decoupled from authorization (apps/common/authorization/).
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.clubs.models import ClubMembership
from tests.accounts.test_account_api import AccountAPITestCase


class SyncHeartbeatAPITests(AccountAPITestCase):
    def setUp(self):
        self.user = self.create_user("heartbeat-user")
        self.other_user = self.create_user("heartbeat-other-user")
        self.club = self.create_club("Heartbeat Club", "heartbeat-club")
        self.other_club = self.create_club(
            "Heartbeat Other Club", "heartbeat-other-club"
        )
        self.court = self.create_court(self.club, "Heartbeat Court")
        self.membership_a = self.create_membership(
            self.user, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.membership_b = self.create_membership(
            self.user, self.other_club, ClubMembership.Role.OWNER
        )
        self.other_membership = self.create_membership(
            self.other_user, self.club, ClubMembership.Role.OWNER
        )

    def heartbeat_url(self):
        return reverse("sync-heartbeat")

    def court_list_url(self, club):
        return reverse("club-court-list", kwargs={"club_slug": club.slug})

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(self.heartbeat_url(), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_heartbeat_requires_no_payload_and_updates_membership(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.heartbeat_url(), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.membership_a.refresh_from_db()
        self.assertIsNotNone(self.membership_a.last_sync_at)
        self.assertIn("last_sync_at", response.data)

    def test_backend_timestamp_is_authoritative_not_client_provided(self):
        self.client.force_authenticate(user=self.user)
        client_supplied_time = timezone.datetime(
            2000, 1, 1, tzinfo=timezone.get_current_timezone()
        )

        response = self.client.post(
            self.heartbeat_url(),
            {
                "last_sync_at": client_supplied_time.isoformat(),
                "timestamp": client_supplied_time.isoformat(),
                "device_id": "some-device",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.membership_a.refresh_from_db()
        self.assertNotEqual(self.membership_a.last_sync_at, client_supplied_time)
        self.assertGreater(
            self.membership_a.last_sync_at,
            client_supplied_time,
        )

    def test_heartbeat_updates_all_active_memberships_for_the_authenticated_user(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.heartbeat_url(), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.membership_a.refresh_from_db()
        self.membership_b.refresh_from_db()
        self.other_membership.refresh_from_db()
        self.assertIsNotNone(self.membership_a.last_sync_at)
        self.assertIsNotNone(self.membership_b.last_sync_at)
        self.assertEqual(self.membership_a.last_sync_at, self.membership_b.last_sync_at)

    def test_heartbeat_does_not_update_other_users_memberships(self):
        self.client.force_authenticate(user=self.user)

        self.client.post(self.heartbeat_url(), {}, format="json")

        self.other_membership.refresh_from_db()
        self.assertIsNone(self.other_membership.last_sync_at)

    def test_normal_api_traffic_does_not_update_last_sync_at(self):
        """
        Regression guard: hitting an ordinary, already-migrated Authorization
        Spine endpoint (Courts) must NOT update last_sync_at as an implicit
        side effect. Only the explicit heartbeat endpoint does.
        """
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.court_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.membership_a.refresh_from_db()
        self.assertIsNone(self.membership_a.last_sync_at)
