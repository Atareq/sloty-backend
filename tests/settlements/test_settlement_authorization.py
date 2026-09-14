"""
Regression tests for Settlement Authorization Spine Migration (Sprint 7).

Validates:
1. Settlement.authorization_config is club-only (never court).
2. SettlementViewSet composes SlotyScopedResourceMixin + SlotyBasePermission.
3. Club isolation (cross-club 403 / out-of-club ID 404).
4. Collector isolation for Staff and Manager without settle flag.
5. Owner / Manager-with-flag / Platform Admin club-wide settlement visibility.
6. Court independence: custody and settlement rows are NOT filtered by
   assigned courts.
7. Direct-object and filter access cannot bypass the scoped queryset.
8. Custom actions (preview, unsettled-summary, mark-settled) keep current
   403 vs 404 behavior.
9. Transaction API remains Club+Court while Settlement/Custody remains
   Club+Collector.
"""

from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.clubs.models import ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from apps.settlements.models import Settlement
from apps.settlements.services import build_custody
from apps.settlements.views import SettlementViewSet
from tests.settlements.test_settlement_api import SettlementAPITestCase

SETTLEMENT_MANAGER_ACTIONS = (
    "list",
    "retrieve",
    "create",
    "preview",
    "unsettled_summary",
    "mark_settled",
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
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.own_settlement = self.create_settlement(
            self.club,
            court=self.court_b,
            collected_by=self.staff,
        )
        self.owner_settlement = self.create_settlement(
            self.club,
            court=self.court_a,
            collected_by=self.owner,
        )
        self.other_settlement = self.create_settlement(
            self.other_club,
            court=self.other_court,
            collected_by=self.owner,
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
        self.assertIn("collected_by", config.select_related)

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
                    self.context_for(self.owner),
                    Settlement,
                    scope=ResourceScope.CLUB,
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
        for role in (Role.ADMIN, Role.OWNER, Role.MANAGER):
            for action in SETTLEMENT_MANAGER_ACTIONS:
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
        self.manager = self.create_user("spine-st-manager")
        self.manager_without_flag = self.create_user("spine-st-manager-denied")
        self.staff = self.create_user("spine-st-staff")
        self.other_staff = self.create_user("spine-st-other-staff")
        self.other_club_user = self.create_user("spine-st-other-club")

        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        self.create_membership(
            self.manager_without_flag,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=False,
        )
        self.create_membership(
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
            ClubMembership.Role.OWNER,
        )

        booking_a = self.create_booking(self.court_a)
        booking_b = self.create_booking(
            self.court_b,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
        )
        other_booking = self.create_booking(self.other_court)

        self.tx_staff_court_a = self.create_transaction(
            booking_a, created_by=self.staff, amount=Decimal("50.00")
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
            self.club,
            court=self.court_b,
            collected_by=self.staff,
        )
        self.other_staff_settlement = self.create_settlement(
            self.club,
            court=self.court_a,
            collected_by=self.other_staff,
        )
        self.manager_settlement = self.create_settlement(
            self.club,
            collected_by=self.manager_without_flag,
        )
        self.other_club_settlement = self.create_settlement(
            self.other_club,
            court=self.other_court,
            collected_by=self.other_club_user,
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

    def test_staff_cannot_retrieve_or_mark_other_collector_settlement(self):
        self.client.force_authenticate(user=self.staff)

        detail = self.client.get(
            self.settlement_detail_url(self.club, self.other_staff_settlement)
        )
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

        mark = self.client.post(
            self.settlement_mark_settled_url(self.club, self.other_staff_settlement),
            {},
            format="json",
        )
        self.assertEqual(mark.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_without_flag_sees_only_own_settlements(self):
        self.client.force_authenticate(user=self.manager_without_flag)

        response = self.client.get(self.settlement_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.manager_settlement.id})

        other_detail = self.client.get(
            self.settlement_detail_url(self.club, self.other_staff_settlement)
        )
        self.assertEqual(other_detail.status_code, status.HTTP_404_NOT_FOUND)

        mark_own = self.client.post(
            self.settlement_mark_settled_url(self.club, self.manager_settlement),
            {},
            format="json",
        )
        self.assertEqual(mark_own.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_and_manager_with_flag_see_all_club_settlements(self):
        expected = {
            self.staff_settlement_unassigned_court.id,
            self.other_staff_settlement.id,
            self.manager_settlement.id,
        }
        for actor in (self.owner, self.manager, self.platform_admin):
            with self.subTest(actor=actor.username):
                self.client.force_authenticate(user=actor)
                response = self.client.get(self.settlement_list_url(self.club))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(self.list_ids(response), expected)
                self.assertNotIn(self.other_club_settlement.id, self.list_ids(response))

    def test_out_of_club_id_is_404_not_existence_leak(self):
        self.client.force_authenticate(user=self.owner)

        detail = self.client.get(
            self.settlement_detail_url(self.club, self.other_club_settlement)
        )
        mark = self.client.post(
            self.settlement_mark_settled_url(self.club, self.other_club_settlement),
            {},
            format="json",
        )
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(mark.status_code, status.HTTP_404_NOT_FOUND)

    def test_filters_cannot_expose_unauthorized_settlements(self):
        self.client.force_authenticate(user=self.staff)

        by_collector = self.client.get(
            self.settlement_list_url(self.club),
            {"collected_by": self.other_staff.id},
        )
        by_court = self.client.get(
            self.settlement_list_url(self.club),
            {"court": self.court_a.id},
        )

        self.assertEqual(by_collector.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.other_staff_settlement.id, self.list_ids(by_collector))
        self.assertNotIn(self.other_staff_settlement.id, self.list_ids(by_court))
        self.assertEqual(
            self.list_ids(by_court),
            set(),
        )

    def test_staff_preview_includes_unassigned_court_collections(self):
        self.client.force_authenticate(user=self.staff)

        preview = self.client.get(self.settlement_preview_url(self.club))
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in preview.data["transactions"]}
        self.assertEqual(ids, {self.tx_staff_court_a.id, self.tx_staff_court_b.id})
        self.assertNotIn(self.tx_other_staff.id, ids)

    def test_staff_cannot_preview_another_collector(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.other_staff.id},
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

        custody = build_custody(club=self.club, collector=self.staff)
        custody_ids = {tx.id for tx in custody["transactions"]}
        self.assertIn(self.tx_staff_court_a.id, custody_ids)
        self.assertIn(self.tx_staff_court_b.id, custody_ids)
        self.assertNotIn(self.tx_other_staff.id, custody_ids)
