"""
Regression tests for the Booking Authorization Spine v2 migration.

Validates:
1. Booking / BookingAttempt authorization_config satisfy the Spine v2 contract.
2. scoped_queryset() enforces club + court isolation without any HTTP layer.
   Bookings are NOT creator-scoped.
3. BookingViewSet / BookingAttemptViewSet compose SlotyScopedResourceMixin
   + SlotyBasePermission and do not override get_queryset() with a manager.
4. Cross-club access is CLUB_ACCESS_REVOKED (403) before any booking is touched.
5. Staff assigned-court isolation returns 404 (no existence leak) for retrieve
   and every lifecycle action.
6. Staff can manage another staff member's booking on the same assigned court.
7. BookingAttempt Staff visibility stays attempted_by=request.user.
8. ROLE_PERMISSIONS covers every migrated BookingViewSet action.
"""

from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.views import BookingAttemptViewSet, BookingViewSet
from apps.clubs.models import ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from tests.bookings.test_booking_api import BookingAPITestCase

BOOKING_VIEWSET_ACTIONS = (
    "list",
    "retrieve",
    "create",
    "partial_update",
    "cancel",
    "complete",
    "no_show",
    "reschedule",
    "expire",
    "end_recurrence",
    "slots",
    "recurrence_next",
    "cancellation_preview",
)

BOOKING_ATTEMPT_VIEWSET_ACTIONS = ("list", "retrieve", "dismiss")

LIFECYCLE_POST_ACTIONS = (
    "cancel",
    "cancellation-preview",
    "complete",
    "no-show",
    "reschedule",
    "expire",
    "end-recurrence",
)


class BookingAuthorizationConfigTests(BookingAPITestCase):
    def setUp(self):
        self.owner = self.create_user("booking-config-owner")
        self.staff = self.create_user("booking-config-staff")
        self.other_staff = self.create_user("booking-config-other-staff")
        self.club = self.create_club("Booking Config Club", slug="booking-config-club")
        self.other_club = self.create_club(
            "Booking Config Other Club", slug="booking-config-other"
        )
        self.court_a = self.create_court(self.club, "Config Court A")
        self.court_b = self.create_court(self.club, "Config Court B")
        self.other_court = self.create_court(self.other_club, "Config Other Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.create_membership(
            self.other_staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.owner_booking = self.create_booking(
            self.court_a, created_by=self.owner, customer_phone="+201000000101"
        )
        self.staff_booking = self.create_booking(
            self.court_a, created_by=self.staff, customer_phone="+201000000102"
        )
        self.other_court_booking = self.create_booking(
            self.court_b, customer_phone="+201000000103"
        )
        self.other_club_booking = self.create_booking(
            self.other_court, customer_phone="+201000000104"
        )

    def context_for(self, user, *, court=None):
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club.slug}/bookings/")
        request.user = user
        return resolve_club_scope(
            request,
            club_slug=self.club.slug,
            court_id=court.pk if court else None,
        )

    def test_booking_config_declares_club_and_court_scope(self):
        config = load_authorization_config(Booking)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertIn("club", config.select_related)
        self.assertIn("court", config.select_related)
        self.assertIn("club_player__player_profile", config.select_related)

    def test_booking_attempt_config_declares_club_and_court_scope(self):
        config = load_authorization_config(BookingAttempt)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertIn("attempted_by", config.select_related)

    def test_booking_club_scope_excludes_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner), Booking, scope=ResourceScope.CLUB
        )

        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(
            ids,
            {
                self.owner_booking.id,
                self.staff_booking.id,
                self.other_court_booking.id,
            },
        )
        self.assertNotIn(self.other_club_booking.id, ids)

    def test_booking_court_scope_restricts_staff_to_assigned_court(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Booking, scope=ResourceScope.COURT
        )

        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(ids, {self.owner_booking.id, self.staff_booking.id})
        self.assertNotIn(self.other_court_booking.id, ids)

    def test_booking_court_scope_is_not_creator_restricted(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Booking, scope=ResourceScope.COURT
        )

        self.assertIn(self.owner_booking.id, queryset.values_list("id", flat=True))

    def test_missing_booking_configuration_never_falls_back_to_unscoped_manager(self):
        original = Booking.authorization_config
        del Booking.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner), Booking, scope=ResourceScope.COURT
                )
        finally:
            Booking.authorization_config = original


class BookingViewSetSpineCompositionTests(BookingAPITestCase):
    def test_booking_viewset_does_not_override_get_queryset(self):
        self.assertIs(
            BookingViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset
        )
        self.assertIs(
            BookingAttemptViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset
        )

    def test_booking_viewset_declares_authorization_model_and_spine_permission(self):
        self.assertIs(BookingViewSet.authorization_model, Booking)
        self.assertIs(BookingAttemptViewSet.authorization_model, BookingAttempt)
        self.assertEqual(BookingViewSet.permission_classes, (SlotyBasePermission,))
        self.assertEqual(
            BookingAttemptViewSet.permission_classes, (SlotyBasePermission,)
        )

    def test_matrix_allows_every_migrated_booking_action_for_club_roles(self):
        for role in (Role.ADMIN, Role.OWNER, Role.MANAGER, Role.STAFF):
            for action in BOOKING_VIEWSET_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(is_action_allowed(role, "BookingViewSet", action))
            for action in BOOKING_ATTEMPT_VIEWSET_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(
                        is_action_allowed(role, "BookingAttemptViewSet", action)
                    )


class BookingAuthorizationAPITests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("booking-auth-admin")
        self.owner = self.create_user("booking-auth-owner")
        self.manager = self.create_user("booking-auth-manager")
        self.staff = self.create_user("booking-auth-staff")
        self.peer_staff = self.create_user("booking-auth-peer-staff")
        self.outsider = self.create_user("booking-auth-outsider")
        self.club = self.create_club("Booking Auth Club", slug="booking-auth-club")
        self.other_club = self.create_club(
            "Booking Auth Other Club", slug="booking-auth-other"
        )
        self.court = self.create_court(self.club, "Auth Court")
        self.same_club_other_court = self.create_court(self.club, "Auth Other Court")
        self.other_court = self.create_court(self.other_club, "Auth External Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.create_membership(
            self.peer_staff, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.create_membership(
            self.outsider, self.other_club, ClubMembership.Role.OWNER
        )
        self.owner_booking = self.create_booking(
            self.court, created_by=self.owner, customer_phone="+201000000201"
        )
        self.staff_booking = self.create_booking(
            self.court, created_by=self.staff, customer_phone="+201000000202"
        )
        self.other_court_booking = self.create_booking(
            self.same_club_other_court,
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000000203",
        )
        self.other_club_booking = self.create_booking(
            self.other_court, customer_phone="+201000000204"
        )

    def post_lifecycle(self, club, booking, action_name, user, payload=None):
        self.client.force_authenticate(user=user)
        return self.client.post(
            self.booking_lifecycle_url(club, booking, action_name),
            payload or {},
            format="json",
        )

    def create_attempt(self, court, attempted_by, **extra_fields):
        data = {
            "club": court.club,
            "court": court,
            "attempted_by": attempted_by,
            "client_request_id": uuid4(),
            "customer_name": "Attempt Customer",
            "customer_phone": "+201000009901",
            "notes": "original attempt",
            "requested_start": self.time_at(20),
            "requested_end": self.time_at(21),
            "requested_at": self.time_at(19),
            "requested_source": Booking.Source.MANUAL,
            "requested_recurring": False,
            "outcome": BookingAttempt.Outcome.REJECTED,
            "failure_code": "BOOKING_SLOT_UNAVAILABLE",
            "failure_details": {"conflict_type": "BOOKING"},
        }
        data.update(extra_fields)
        return BookingAttempt.objects.create(**data)

    def test_outsider_cannot_list_selected_club_bookings(self):
        self.client.force_authenticate(user=self.outsider)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")

    def test_staff_lists_assigned_court_bookings_including_peer_created(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.owner_booking.id, self.staff_booking.id},
        )
        self.assertNotIn(self.other_court_booking.id, self.list_ids(response))

    def test_staff_can_retrieve_peer_booking_on_assigned_court(self):
        self.client.force_authenticate(user=self.peer_staff)

        response = self.client.get(
            self.booking_detail_url(self.club, self.staff_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.staff_booking.id)
        self.assertEqual(
            response.data["customer_name"],
            self.staff_booking.club_player.display_name,
        )
        self.assertIn("customer_phone", response.data)

    def test_staff_cannot_retrieve_other_court_booking(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.booking_detail_url(self.club, self.other_court_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_lifecycle_actions_on_other_court_are_404(self):
        for action_name in LIFECYCLE_POST_ACTIONS:
            with self.subTest(action=action_name):
                payload = {}
                if action_name == "reschedule":
                    payload = {
                        "court": self.court.id,
                        "start_time": self.time_at(22).isoformat(),
                        "end_time": self.time_at(23).isoformat(),
                    }
                elif action_name == "no-show":
                    payload = {"reason": "Customer did not arrive"}
                response = self.post_lifecycle(
                    self.club,
                    self.other_court_booking,
                    action_name,
                    self.staff,
                    payload,
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.staff)
        next_response = self.client.get(
            self.booking_lifecycle_url(
                self.club, self.other_court_booking, "recurrence-next"
            )
        )
        self.assertEqual(next_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_can_complete_peer_booking_on_assigned_court(self):
        booking = self.create_booking(
            self.court,
            created_by=self.staff,
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000000205",
        )
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(self.club, booking, "complete", self.peer_staff)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)

    def test_owner_and_staff_can_patch_customer_snapshot_fields(self):
        for actor in (self.owner, self.staff):
            with self.subTest(actor=actor.username):
                self.client.force_authenticate(user=actor)
                response = self.client.patch(
                    self.booking_detail_url(self.club, self.staff_booking),
                    {"notes": f"patched by {actor.username}"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["notes"], f"patched by {actor.username}")

    def test_staff_attempt_queryset_stays_attempted_by_self(self):
        own_attempt = self.create_attempt(self.court, self.staff)
        peer_attempt = self.create_attempt(
            self.court,
            self.peer_staff,
            requested_start=self.time_at(21),
            requested_end=self.time_at(22),
        )
        other_court_own_attempt = self.create_attempt(
            self.same_club_other_court,
            self.staff,
            requested_start=self.time_at(22),
            requested_end=self.time_at(23),
        )
        self.client.force_authenticate(user=self.staff)

        list_response = self.client.get(self.booking_attempt_list_url(self.club))
        peer_detail = self.client.get(
            self.booking_attempt_detail_url(self.club, peer_attempt)
        )
        other_court_detail = self.client.get(
            self.booking_attempt_detail_url(self.club, other_court_own_attempt)
        )
        dismiss_peer = self.client.post(
            self.booking_attempt_dismiss_url(self.club, peer_attempt),
            {},
            format="json",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in list_response.data["results"]}, {own_attempt.id}
        )
        self.assertEqual(peer_detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(other_court_detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(dismiss_peer.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_list_peer_attempts_but_cannot_dismiss_them(self):
        attempt = self.create_attempt(self.court, self.staff)
        self.client.force_authenticate(user=self.owner)

        list_response = self.client.get(self.booking_attempt_list_url(self.club))
        detail_response = self.client.get(
            self.booking_attempt_detail_url(self.club, attempt)
        )
        dismiss_response = self.client.post(
            self.booking_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            attempt.id, {item["id"] for item in list_response.data["results"]}
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(dismiss_response.status_code, status.HTTP_403_FORBIDDEN)
