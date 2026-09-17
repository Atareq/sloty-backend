"""
Regression tests for Settlement Authorization Spine Migration.

Validates:
1. Settlement.authorization_config is club-only (never court).
2. SettlementViewSet composes SlotyScopedResourceMixin + SlotyBasePermission.
3. Club isolation (cross-club 403 / out-of-club ID 404).
4. Scope isolation for Staff.
5. Owner / Platform Admin club-wide settlement visibility.
6. Court independence: custody and settlement rows are NOT filtered by
   assigned courts.
7. Direct-object and filter access cannot bypass the scoped queryset.
8. Custom actions (preview, unsettled-summary) keep current
   403 vs 404 behavior.
9. Transaction API remains Club+Court while Settlement/Custody remains
   Club+User.
"""

from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import preview as preview_settlement
from apps.settlements.views import SettlementViewSet
from tests.profile_factories import grant_owner_club, grant_staff_court
from tests.settlements.test_settlement_api import SettlementAPITestCase

SETTLEMENT_OWNER_ACTIONS = (
    "list",
    "retrieve",
    "create",
    "preview",
    "unsettled_summary",
)
SETTLEMENT_STAFF_ACTIONS = ("list", "retrieve", "preview")


class SettlementAuthorizationConfigTests(SettlementAPITestCase):

    def setUp(self):
        self.owner = self.create_user("st-config-owner")
        self.staff = self.create_user("st-config-staff")
        self.club = self.create_club("St Config Club", slug="st-config-club")
        self.other_club = self.create_club(
            "St Config Other Club", slug="st-config-other"
        )
        self.court_a = self.create_court(self.club, "St Config Court A")
        self.court_b = self.create_court(self.club, "St Config Court B")
        self.other_court = self.create_court(self.other_club, "St Config Other Court")
        grant_owner_club(self.owner, self.club)
        grant_staff_court(self.staff, self.court_a)
        self.own_settlement = self.create_settlement(
            self.club, court=self.court_b, settled_by=self.staff
        )
        self.owner_settlement = self.create_settlement(
            self.club, court=self.court_a, settled_by=self.owner
        )
        self.other_settlement = self.create_settlement(
            self.other_club, court=self.other_court, settled_by=self.owner
        )

    def context_for(self, user):
        request = APIRequestFactory().get(
            f"/api/v1/clubs/{self.club.slug}/settlements/"
        )
        request.user = user
        return resolve_club_scope(request, club_slug=self.club.slug)

    def test_settlement_config_declares_club_scope_only(self):
        config = load_authorization_config(Settlement)
        self.assertEqual(config.default_scope, ResourceScope.CLUB.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertNotIn(ResourceScope.COURT.value, config.scopes)
        self.assertIn("settled_by", config.select_related)
        self.assertNotIn("collected_by", config.select_related)

    def test_settlement_club_scope_excludes_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner), Settlement, scope=ResourceScope.CLUB
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(ids, {self.own_settlement.id, self.owner_settlement.id})
        self.assertNotIn(self.other_settlement.id, ids)

    def test_spine_club_scope_is_not_court_restricted(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Settlement, scope=ResourceScope.CLUB
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertIn(self.own_settlement.id, ids)
        self.assertIn(self.owner_settlement.id, ids)

    def test_missing_settlement_configuration_never_falls_back(self):
        original = Settlement.authorization_config
        del Settlement.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner), Settlement, scope=ResourceScope.CLUB
                )
        finally:
            Settlement.authorization_config = original


class SettlementViewSetSpineCompositionTests(SettlementAPITestCase):

    def test_settlement_viewset_does_not_override_get_queryset(self):
        self.assertIs(
            SettlementViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset
        )

    def test_settlement_viewset_declares_club_scope_and_spine_permission(self):
        self.assertIs(SettlementViewSet.authorization_model, Settlement)
        self.assertEqual(SettlementViewSet.authorization_scope, ResourceScope.CLUB)
        self.assertEqual(SettlementViewSet.permission_classes, (SlotyBasePermission,))

    def test_matrix_matches_approved_settlement_actions(self):
        for role in (Role.ADMIN, Role.OWNER):
            for action in SETTLEMENT_OWNER_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(
                        is_action_allowed(role, "SettlementViewSet", action)
                    )
        for action in SETTLEMENT_STAFF_ACTIONS:
            self.assertTrue(is_action_allowed(Role.STAFF, "SettlementViewSet", action))
        self.assertFalse(is_action_allowed(Role.STAFF, "SettlementViewSet", "create"))
        self.assertFalse(
            is_action_allowed(Role.STAFF, "SettlementViewSet", "unsettled_summary")
        )
        self.assertFalse(
            is_action_allowed(Role.STAFF, "SettlementViewSet", "mark_settled")
        )
        self.assertFalse(
            is_action_allowed(Role.OWNER, "SettlementViewSet", "mark_settled")
        )
        self.assertFalse(
            is_action_allowed(Role.ADMIN, "SettlementViewSet", "mark_settled")
        )
        self.assertFalse(
            is_action_allowed(Role.OWNER, "SettlementViewSet", "partial_update")
        )
        self.assertFalse(is_action_allowed(Role.OWNER, "SettlementViewSet", "destroy"))


class SettlementAuthorizationSpineTests(SettlementAPITestCase):

    def setUp(self):
        self.club = self.create_club("Spine Settlement Club", "spine-settlement")
        self.other_club = self.create_club("Other Settlement Club", "other-settlement")
        self.court_a = self.create_court(self.club, "Court A")
        self.court_b = self.create_court(self.club, "Court B")
        self.other_court = self.create_court(self.other_club, "Other Club Court")
        self.platform_admin = self.create_platform_admin("spine-st-admin")
        self.owner = self.create_user("spine-st-owner")
        self.staff = self.create_user("spine-st-staff")
        self.other_staff = self.create_user("spine-st-other-staff")
        self.other_club_user = self.create_user("spine-st-other-club")
        grant_owner_club(self.owner, self.club)
        grant_staff_court(self.staff, self.court_a)
        grant_staff_court(self.other_staff, self.court_b)
        grant_owner_club(self.other_club_user, self.other_club)
        booking_a = self.create_booking(self.court_a)
        booking_b = self.create_booking(
            self.court_b, start_time=self.time_at(18), end_time=self.time_at(19)
        )
        other_booking = self.create_booking(self.other_court)
        self.tx_staff_court_a = self.create_transaction(
            booking_a, created_by=self.staff, amount=Decimal("50.00")
        )
        tx_staff_court_b_settled = self.create_transaction(
            booking_b, created_by=self.staff, amount=Decimal("70.00")
        )
        self.tx_staff_court_b = self.create_transaction(
            booking_b, created_by=self.staff, amount=Decimal("70.00")
        )
        self.tx_other_staff = self.create_transaction(
            booking_b, created_by=self.other_staff, amount=Decimal("90.00")
        )
        self.create_transaction(
            other_booking, created_by=self.other_club_user, amount=Decimal("40.00")
        )
        self.staff_settlement_unassigned_court = self.create_settlement(
            self.club, court=self.court_b, settled_by=self.staff
        )
        SettlementTransaction.objects.create(
            settlement=self.staff_settlement_unassigned_court,
            transaction=tx_staff_court_b_settled,
            amount=tx_staff_court_b_settled.amount,
        )
        self.other_staff_settlement = self.create_settlement(
            self.club, court=self.court_a, settled_by=self.other_staff
        )
        SettlementTransaction.objects.create(
            settlement=self.other_staff_settlement,
            transaction=self.tx_other_staff,
            amount=self.tx_other_staff.amount,
        )
        self.other_club_settlement = self.create_settlement(
            self.other_club, court=self.other_court, settled_by=self.other_club_user
        )

    def test_club_isolation_enforced(self):
        self.client.force_authenticate(user=self.other_club_user)
        response = self.client.get(self.settlement_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")

    def test_staff_sees_only_own_settlements_including_unassigned_court_row(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.settlement_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response), {self.staff_settlement_unassigned_court.id}
        )
        own_detail = self.client.get(
            self.settlement_detail_url(
                self.club, self.staff_settlement_unassigned_court
            )
        )
        self.assertEqual(own_detail.status_code, status.HTTP_200_OK)

    def test_staff_cannot_retrieve_other_user_settlement(self):
        self.client.force_authenticate(user=self.staff)
        detail = self.client.get(
            self.settlement_detail_url(self.club, self.other_staff_settlement)
        )
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_out_of_club_id_is_404_not_existence_leak(self):
        self.client.force_authenticate(user=self.owner)
        detail = self.client.get(
            self.settlement_detail_url(self.club, self.other_club_settlement)
        )
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_filters_cannot_expose_unauthorized_settlements(self):
        self.client.force_authenticate(user=self.staff)
        by_user = self.client.get(
            self.settlement_list_url(self.club), {"user": self.other_staff.id}
        )
        by_court = self.client.get(
            self.settlement_list_url(self.club), {"court": self.court_a.id}
        )
        self.assertEqual(by_user.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.other_staff_settlement.id, self.list_ids(by_user))
        self.assertNotIn(self.other_staff_settlement.id, self.list_ids(by_court))
        self.assertEqual(self.list_ids(by_court), set())

    def test_staff_preview_includes_unassigned_court_collections(self):
        self.client.force_authenticate(user=self.staff)
        preview = self.client.get(self.settlement_preview_url(self.club))
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in preview.data["transactions"]}
        self.assertEqual(ids, {self.tx_staff_court_a.id, self.tx_staff_court_b.id})
        self.assertNotIn(self.tx_other_staff.id, ids)

    def test_staff_cannot_preview_another_user(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(
            self.settlement_preview_url(self.club),
            {"user": self.other_staff.id},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_access_unsettled_summary(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.settlement_unsettled_summary_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_transaction_api_stays_court_scoped_while_custody_does_not(self):
        from django.urls import reverse

        self.client.force_authenticate(user=self.staff)
        tx_list = self.client.get(
            reverse("club-transaction-list", kwargs={"club_slug": self.club.slug})
        )
        tx_ids = {row["id"] for row in tx_list.data["results"]}
        self.assertIn(self.tx_staff_court_a.id, tx_ids)
        self.assertNotIn(self.tx_staff_court_b.id, tx_ids)
        preview_res = preview_settlement(club=self.club, user=self.staff)
        custody_ids = {tx["id"] for tx in preview_res["transactions"]}
        self.assertIn(self.tx_staff_court_a.id, custody_ids)
        self.assertIn(self.tx_staff_court_b.id, custody_ids)
        self.assertNotIn(self.tx_other_staff.id, custody_ids)
