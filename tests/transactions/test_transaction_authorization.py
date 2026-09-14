"""
Regression tests for Transaction Authorization Spine Migration (Task 4).

Validates:
1. Staff operational access is constrained to assigned court(s) and own transactions.
2. Direct detail/cancel lookup cannot bypass court authorization (404).
3. Creation on unauthorized court is denied (403).
4. Cross-club access is denied (CLUB_ACCESS_REVOKED - 403).
5. Owner / Manager club-wide visibility and cancellation rules.
6. Platform Admin cancellation authority.
7. Transaction Attempt scoping and dismissal authority.
8. Custody separation: Staff Current Custody includes all-court collections
   (Club + Collector) despite operational transaction court scoping.
9. Spine v2 authorization_config + SlotyScopedResourceMixin composition.
10. Search/filter cannot widen the authorized queryset.
"""

from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.bookings.models import Booking
from apps.clubs.models import ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from apps.settlements.services import build_custody
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.views import TransactionAttemptViewSet, TransactionViewSet
from tests.transactions.test_transaction_api import TransactionAPITestCase

TRANSACTION_VIEWSET_ACTIONS = ("list", "retrieve", "create", "cancel")
TRANSACTION_ATTEMPT_VIEWSET_ACTIONS = ("list", "retrieve", "dismiss")


class TransactionAuthorizationConfigTests(TransactionAPITestCase):
    def setUp(self):
        self.owner = self.create_user("tx-config-owner")
        self.staff = self.create_user("tx-config-staff")
        self.club = self.create_club("Tx Config Club", slug="tx-config-club")
        self.other_club = self.create_club(
            "Tx Config Other Club", slug="tx-config-other"
        )
        self.court_a = self.create_court(self.club, "Tx Config Court A")
        self.court_b = self.create_court(self.club, "Tx Config Court B")
        self.other_court = self.create_court(self.other_club, "Tx Config Other Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        booking_a = self.create_booking(self.court_a, customer_phone="+201000000301")
        booking_b = self.create_booking(
            self.court_b,
            customer_phone="+201000000302",
            start_time=self.time_at(18),
            end_time=self.time_at(19),
        )
        other_booking = self.create_booking(
            self.other_court, customer_phone="+201000000303"
        )
        self.tx_a = self.create_transaction(booking_a, created_by=self.owner)
        self.tx_b = self.create_transaction(booking_b, created_by=self.owner)
        self.tx_other = self.create_transaction(other_booking, created_by=self.owner)

    def context_for(self, user):
        request = APIRequestFactory().get(
            f"/api/v1/clubs/{self.club.slug}/transactions/"
        )
        request.user = user
        return resolve_club_scope(request, club_slug=self.club.slug)

    def test_transaction_config_declares_club_and_court_scope(self):
        config = load_authorization_config(Transaction)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertIn("booking__club_player__player_profile", config.select_related)

    def test_transaction_attempt_config_declares_club_and_court_scope(self):
        config = load_authorization_config(TransactionAttempt)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertIn("attempted_by", config.select_related)

    def test_transaction_club_scope_excludes_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner), Transaction, scope=ResourceScope.CLUB
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(ids, {self.tx_a.id, self.tx_b.id})
        self.assertNotIn(self.tx_other.id, ids)

    def test_transaction_court_scope_restricts_staff_to_assigned_court(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Transaction, scope=ResourceScope.COURT
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(ids, {self.tx_a.id})
        self.assertNotIn(self.tx_b.id, ids)

    def test_spine_court_scope_is_not_creator_restricted(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Transaction, scope=ResourceScope.COURT
        )
        self.assertIn(self.tx_a.id, queryset.values_list("id", flat=True))

    def test_missing_transaction_configuration_never_falls_back(self):
        original = Transaction.authorization_config
        del Transaction.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner),
                    Transaction,
                    scope=ResourceScope.COURT,
                )
        finally:
            Transaction.authorization_config = original


class TransactionViewSetSpineCompositionTests(TransactionAPITestCase):
    def test_transaction_viewset_does_not_override_get_queryset(self):
        self.assertIs(
            TransactionViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset
        )
        self.assertIs(
            TransactionAttemptViewSet.get_queryset,
            SlotyScopedResourceMixin.get_queryset,
        )

    def test_transaction_viewset_declares_authorization_model_and_spine_permission(
        self,
    ):
        self.assertIs(TransactionViewSet.authorization_model, Transaction)
        self.assertIs(TransactionAttemptViewSet.authorization_model, TransactionAttempt)
        self.assertEqual(TransactionViewSet.permission_classes, (SlotyBasePermission,))
        self.assertEqual(
            TransactionAttemptViewSet.permission_classes, (SlotyBasePermission,)
        )

    def test_matrix_allows_every_migrated_transaction_action_for_club_roles(self):
        for role in (Role.ADMIN, Role.OWNER, Role.MANAGER, Role.STAFF):
            for action in TRANSACTION_VIEWSET_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(
                        is_action_allowed(role, "TransactionViewSet", action)
                    )
            for action in TRANSACTION_ATTEMPT_VIEWSET_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(
                        is_action_allowed(role, "TransactionAttemptViewSet", action)
                    )
            self.assertFalse(
                is_action_allowed(role, "TransactionViewSet", "partial_update")
            )
            self.assertFalse(is_action_allowed(role, "TransactionViewSet", "destroy"))


class TransactionAuthorizationSpineTests(TransactionAPITestCase):
    def setUp(self):
        self.club = self.create_club("Spine Club", "spine-club")
        self.other_club = self.create_club("Other Club", "other-club")

        self.court_a = self.create_court(self.club, "Court A")
        self.court_b = self.create_court(self.club, "Court B")
        self.other_court = self.create_court(self.other_club, "Other Club Court")

        self.platform_admin = self.create_platform_admin("spine-admin")
        self.owner = self.create_user("spine-owner")
        self.manager = self.create_user("spine-manager")
        self.staff = self.create_user("spine-staff")
        self.other_staff = self.create_user("spine-other-staff")
        self.other_club_user = self.create_user("other-club-user")

        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.staff_membership = self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court_a,
        )
        self.create_membership(
            self.other_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court_b,
        )
        self.create_membership(
            self.other_club_user,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )

        # Bookings
        self.booking_a = self.create_booking(
            self.court_a,
            customer_name="Booking Court A",
            status=Booking.Status.CONFIRMED,
        )
        self.booking_b = self.create_booking(
            self.court_b,
            customer_name="Booking Court B",
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
        )

        # Transactions
        self.tx_court_a_staff = self.create_transaction(
            self.booking_a,
            amount=Decimal("50.00"),
            created_by=self.staff,
        )
        self.tx_court_a_owner = self.create_transaction(
            self.booking_a,
            amount=Decimal("60.00"),
            created_by=self.owner,
        )
        self.tx_court_b_other_staff = self.create_transaction(
            self.booking_b,
            amount=Decimal("70.00"),
            created_by=self.other_staff,
        )

    def create_attempt(self, booking, attempted_by, **extra_fields):
        data = {
            "club": booking.club,
            "court": booking.court,
            "booking": booking,
            "attempted_by": attempted_by,
            "client_request_id": uuid4(),
            "amount": Decimal("500.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
            "payment_reference": "ATTEMPT-REF",
            "notes": "original payment attempt",
            "occurred_at": self.time_at(19),
            "outcome": TransactionAttempt.Outcome.REJECTED,
            "failure_code": "PAYMENT_AMOUNT_EXCEEDS_REMAINING",
            "failure_details": {"amount": ["Too much"]},
        }
        data.update(extra_fields)
        return TransactionAttempt.objects.create(**data)

    def attempt_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def cancel_url(self, club, transaction_obj):
        return reverse(
            "club-transaction-cancel",
            kwargs={"club_slug": club.slug, "pk": transaction_obj.pk},
        )

    def test_staff_allowed_court_access(self):
        """Staff has access to Court A and own transactions."""
        self.client.force_authenticate(user=self.staff)

        # List
        response = self.client.get(self.transaction_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.tx_court_a_staff.id})

        # Detail
        detail_res = self.client.get(
            self.transaction_detail_url(self.club, self.tx_court_a_staff)
        )
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)

        # Create on assigned court
        booking = self.create_booking(
            self.court_a,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.CONFIRMED,
        )
        create_res = self.post_transaction(self.club, booking)
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)

        # Cancel own transaction
        cancel_res = self.client.post(
            self.cancel_url(self.club, self.tx_court_a_staff),
            {"reason": "Entry error"},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)

    def test_staff_unauthorized_court_denied(self):
        """Staff is denied list, detail, create, and cancel for unauthorized Court B."""
        self.client.force_authenticate(user=self.staff)

        # List does not leak Court B
        list_res = self.client.get(self.transaction_list_url(self.club))
        self.assertNotIn(self.tx_court_b_other_staff.id, self.list_ids(list_res))

        # Detail on Court B is 404
        detail_res = self.client.get(
            self.transaction_detail_url(self.club, self.tx_court_b_other_staff)
        )
        self.assertEqual(detail_res.status_code, status.HTTP_404_NOT_FOUND)

        # Cancel on Court B is 404
        cancel_res = self.client.post(
            self.cancel_url(self.club, self.tx_court_b_other_staff),
            {"reason": "Unauthorized attempt"},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_404_NOT_FOUND)

        # Create on Court B is 403
        create_res = self.post_transaction(self.club, self.booking_b)
        self.assertEqual(create_res.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_access_other_collector_on_assigned_court(self):
        """
        Staff cannot see or cancel transactions created by other collectors
        even on assigned court.
        """
        self.client.force_authenticate(user=self.staff)

        # Not in list
        list_res = self.client.get(self.transaction_list_url(self.club))
        self.assertNotIn(self.tx_court_a_owner.id, self.list_ids(list_res))

        # Detail returns 404
        detail_res = self.client.get(
            self.transaction_detail_url(self.club, self.tx_court_a_owner)
        )
        self.assertEqual(detail_res.status_code, status.HTTP_404_NOT_FOUND)

        # Cancel returns 404
        cancel_res = self.client.post(
            self.cancel_url(self.club, self.tx_court_a_owner),
            {"reason": "Cancel other"},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_404_NOT_FOUND)

    def test_club_isolation_enforced(self):
        """Staff from another club cannot access this club's transactions."""
        self.client.force_authenticate(user=self.other_club_user)

        response = self.client.get(self.transaction_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")

    def test_owner_and_manager_scoping(self):
        """
        Owner and Manager see all club transactions across all courts,
        but can only cancel own.
        """
        for actor in (self.owner, self.manager):
            with self.subTest(actor=actor.username):
                self.client.force_authenticate(user=actor)

                # List includes all club transactions
                list_res = self.client.get(self.transaction_list_url(self.club))
                self.assertEqual(list_res.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    self.list_ids(list_res),
                    {
                        self.tx_court_a_staff.id,
                        self.tx_court_a_owner.id,
                        self.tx_court_b_other_staff.id,
                    },
                )

                # Detail on any club transaction is allowed
                detail_res = self.client.get(
                    self.transaction_detail_url(self.club, self.tx_court_b_other_staff)
                )
                self.assertEqual(detail_res.status_code, status.HTTP_200_OK)

                # Cannot cancel other collector's transaction -> 403
                cancel_res = self.client.post(
                    self.cancel_url(self.club, self.tx_court_a_staff),
                    {"reason": "Cannot cancel other"},
                    format="json",
                )
                self.assertEqual(cancel_res.status_code, status.HTTP_403_FORBIDDEN)

        # Owner can cancel own transaction
        self.client.force_authenticate(user=self.owner)
        owner_cancel = self.client.post(
            self.cancel_url(self.club, self.tx_court_a_owner),
            {"reason": "Cancel own"},
            format="json",
        )
        self.assertEqual(owner_cancel.status_code, status.HTTP_200_OK)

    def test_platform_admin_can_cancel_any_transaction(self):
        """Platform Admin can cancel any eligible transaction in the club."""
        self.client.force_authenticate(user=self.platform_admin)

        cancel_res = self.client.post(
            self.cancel_url(self.club, self.tx_court_b_other_staff),
            {"reason": "Admin correction"},
            format="json",
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)

    def test_transaction_attempt_authorization(self):
        """TransactionAttempt scoping and dismissal authority."""
        staff_attempt = self.create_attempt(self.booking_a, self.staff)
        other_attempt = self.create_attempt(self.booking_b, self.other_staff)

        # Staff lists only own attempt
        self.client.force_authenticate(user=self.staff)
        list_res = self.client.get(self.transaction_attempt_list_url(self.club))
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertEqual(self.attempt_ids(list_res), {staff_attempt.id})
        self.assertNotIn(other_attempt.id, self.attempt_ids(list_res))

        # Staff cannot dismiss other staff's attempt -> 404
        dismiss_other = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, other_attempt),
            {},
            format="json",
        )
        self.assertEqual(dismiss_other.status_code, status.HTTP_404_NOT_FOUND)

        # Owner cannot dismiss staff's attempt -> 403
        self.client.force_authenticate(user=self.owner)
        dismiss_by_owner = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, staff_attempt),
            {},
            format="json",
        )
        self.assertEqual(dismiss_by_owner.status_code, status.HTTP_403_FORBIDDEN)

        # Staff can dismiss own rejected attempt
        self.client.force_authenticate(user=self.staff)
        dismiss_own = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, staff_attempt),
            {},
            format="json",
        )
        self.assertEqual(dismiss_own.status_code, status.HTTP_200_OK)

    def test_custody_separation_regression(self):
        """
        Verify that Current Custody (Club + Collector) includes all collections
        across all courts for a staff member, completely independent of
        operational transaction court scoping.
        """
        # Staff collected tx1 on Court A (assigned court)
        # Staff also collected tx2 on Court B (unassigned court)
        tx_court_b_staff = Transaction.objects.create(
            club=self.club,
            court=self.court_b,
            booking=self.booking_b,
            amount=Decimal("120.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.staff,
        )

        # 1. Operational Transaction API: Staff sees ONLY Court A
        self.client.force_authenticate(user=self.staff)
        tx_list = self.client.get(self.transaction_list_url(self.club))
        self.assertIn(self.tx_court_a_staff.id, self.list_ids(tx_list))
        self.assertNotIn(tx_court_b_staff.id, self.list_ids(tx_list))

        # 2. Financial Custody (Settlement Domain):
        # Staff custody includes BOTH Court A and Court B
        custody = build_custody(club=self.club, collector=self.staff)
        candidate_ids = {tx.id for tx in custody["transactions"]}
        self.assertIn(self.tx_court_a_staff.id, candidate_ids)
        self.assertIn(tx_court_b_staff.id, candidate_ids)
        self.assertEqual(
            custody["net_amount"],
            self.tx_court_a_staff.amount + tx_court_b_staff.amount,
        )

    def test_filters_cannot_expose_unauthorized_transactions(self):
        self.client.force_authenticate(user=self.staff)

        by_court = self.client.get(
            self.transaction_list_url(self.club),
            {"court": self.court_b.id},
        )
        by_creator = self.client.get(
            self.transaction_list_url(self.club),
            {"created_by": self.owner.id},
        )
        by_search = self.client.get(
            self.transaction_list_url(self.club),
            {"search": "Booking Court B"},
        )

        self.assertEqual(by_court.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.tx_court_b_other_staff.id, self.list_ids(by_court))
        self.assertNotIn(self.tx_court_a_owner.id, self.list_ids(by_creator))
        self.assertNotIn(self.tx_court_b_other_staff.id, self.list_ids(by_search))
