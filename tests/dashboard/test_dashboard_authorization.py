"""
Regression tests for Dashboard Authorization Spine Migration (Sprint 8).

Dashboard is a read model. These tests prove:
1. Authenticated endpoints compose ClubScopedViewMixin + SlotyBasePermission.
2. Matrix actions match DashboardViewSet (not GenericAPIView class names).
3. Club isolation of aggregations (Club B totals cannot inflate Club A).
4. Staff operational metrics are court-scoped; financial endpoints stay 403.
5. Owner/Manager financial aggregations are club-wide, not Staff-assignment.
6. Current custody is Club + Collector unless an explicit court filter is
   supplied — never implicit Staff court assignment.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status

from apps.clubs.models import ClubMembership
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.roles import Role
from apps.dashboard.views import (
    ClubCalendarAPIView,
    CourtAvailabilityAPIView,
    CourtUtilizationAPIView,
    DashboardAPIView,
    DashboardOverviewAPIView,
    DashboardRevenueAPIView,
    DashboardSummaryAPIView,
)
from apps.settlements.services import build_custody
from tests.dashboard.test_dashboard_api import DashboardAPITestCase

DASHBOARD_FINANCIAL_ACTIONS = ("overview", "revenue", "court_utilization")
DASHBOARD_OPERATIONAL_ACTIONS = ("summary", "calendar", "availability")


class DashboardSpineCompositionTests(DashboardAPITestCase):
    def test_authenticated_dashboard_views_use_spine_permission(self):
        self.assertTrue(issubclass(DashboardAPIView, ClubScopedViewMixin))
        self.assertEqual(DashboardAPIView.permission_classes, (SlotyBasePermission,))
        self.assertEqual(DashboardAPIView.permission_view_name, "DashboardViewSet")
        self.assertEqual(DashboardSummaryAPIView.action, "summary")
        self.assertEqual(DashboardOverviewAPIView.action, "overview")
        self.assertEqual(DashboardRevenueAPIView.action, "revenue")
        self.assertEqual(CourtUtilizationAPIView.action, "court_utilization")
        self.assertEqual(ClubCalendarAPIView.action, "calendar")
        self.assertEqual(CourtAvailabilityAPIView.action, "availability")

    def test_matrix_matches_dashboard_actions(self):
        for role in (Role.ADMIN, Role.OWNER, Role.MANAGER):
            for action in DASHBOARD_FINANCIAL_ACTIONS + DASHBOARD_OPERATIONAL_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(is_action_allowed(role, "DashboardViewSet", action))
        for action in DASHBOARD_OPERATIONAL_ACTIONS:
            self.assertTrue(is_action_allowed(Role.STAFF, "DashboardViewSet", action))
        for action in DASHBOARD_FINANCIAL_ACTIONS:
            self.assertFalse(is_action_allowed(Role.STAFF, "DashboardViewSet", action))


class DashboardAuthorizationSpineTests(DashboardAPITestCase):
    def setUp(self):
        self.club_a = self.create_club("Dash Club A", "dash-club-a")
        self.club_b = self.create_club("Dash Club B", "dash-club-b")
        self.court_a = self.create_court(self.club_a, "Court A")
        self.court_b = self.create_court(self.club_a, "Court B")
        self.court_other = self.create_court(self.club_b, "Other Club Court")

        self.owner_a = self.create_user("dash-owner-a")
        self.staff_a = self.create_user("dash-staff-a")
        self.owner_b = self.create_user("dash-owner-b")
        self.create_membership(self.owner_a, self.club_a, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff_a,
            self.club_a,
            ClubMembership.Role.STAFF,
            court=self.court_a,
        )
        self.create_membership(self.owner_b, self.club_b, ClubMembership.Role.OWNER)

        booking_a = self.create_booking(
            self.court_a,
            total_price=Decimal("100.00"),
            status="CONFIRMED",
        )
        booking_b = self.create_booking(
            self.court_b,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            total_price=Decimal("200.00"),
            status="CONFIRMED",
        )
        booking_other = self.create_booking(
            self.court_other,
            total_price=Decimal("900.00"),
            status="CONFIRMED",
        )
        self.tx_staff_assigned = self.create_transaction(
            booking_a,
            amount=Decimal("40.00"),
            created_by=self.staff_a,
        )
        self.tx_staff_unassigned_court = self.create_transaction(
            booking_b,
            amount=Decimal("60.00"),
            created_by=self.staff_a,
        )
        self.create_transaction(
            booking_other,
            amount=Decimal("900.00"),
            created_by=self.owner_b,
        )

    def test_club_isolation_rejects_other_club_member(self):
        self.client.force_authenticate(user=self.owner_b)

        response = self.client.get(self.summary_url(self.club_a), self.range_params())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")

    def test_club_a_aggregations_exclude_club_b_totals(self):
        self.client.force_authenticate(user=self.owner_a)

        overview = self.client.get(self.overview_url(self.club_a), self.range_params())
        revenue = self.client.get(self.revenue_url(self.club_a), self.range_params())
        self.assertEqual(overview.status_code, status.HTTP_200_OK)
        self.assertEqual(overview.data["total_booking_value"], "300.00")
        self.assertEqual(overview.data["transaction_total"], "100.00")
        self.assertNotEqual(overview.data["total_booking_value"], "1200.00")

        period_total = sum(
            Decimal(row["transaction_total"]) for row in revenue.data["results"]
        )
        self.assertEqual(period_total, Decimal("100.00"))

    def test_staff_operational_metrics_are_court_scoped(self):
        self.client.force_authenticate(user=self.staff_a)

        summary = self.client.get(self.summary_url(self.club_a), self.range_params())
        calendar = self.client.get(self.calendar_url(self.club_a), self.range_params())
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data["scope"]["court_ids"], [self.court_a.id])
        self.assertEqual(summary.data["summary"]["total_bookings"], 1)
        self.assertFalse(summary.data["scope"]["financial_visible"])
        self.assertIsNone(summary.data["summary"]["transaction_total"])

        calendar_courts = {item["court"] for item in calendar.data["items"]}
        self.assertEqual(calendar_courts, {self.court_a.id})

        other_court_availability = self.client.get(
            self.availability_url(self.club_a, self.court_b),
            {"date": "2026-07-06"},
        )
        self.assertEqual(
            other_court_availability.status_code, status.HTTP_403_FORBIDDEN
        )

    def test_staff_financial_endpoints_remain_forbidden(self):
        self.client.force_authenticate(user=self.staff_a)
        for url in (
            self.overview_url(self.club_a),
            self.revenue_url(self.club_a),
            self.utilization_url(self.club_a),
        ):
            response = self.client.get(url, self.range_params())
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_financials_include_unassigned_court_collections(self):
        self.client.force_authenticate(user=self.owner_a)

        overview = self.client.get(self.overview_url(self.club_a), self.range_params())
        self.assertEqual(overview.status_code, status.HTTP_200_OK)
        self.assertEqual(overview.data["transaction_total"], "100.00")
        self.assertEqual(overview.data["unsettled_transaction_count"], 2)
        self.assertEqual(overview.data["unsettled_transaction_total_amount"], "100.00")

    def test_transaction_api_stays_court_scoped_while_dashboard_custody_does_not(self):
        self.client.force_authenticate(user=self.staff_a)
        tx_list = self.client.get(
            reverse("club-transaction-list", kwargs={"club_slug": self.club_a.slug})
        )
        tx_ids = {row["id"] for row in tx_list.data["results"]}
        self.assertIn(self.tx_staff_assigned.id, tx_ids)
        self.assertNotIn(self.tx_staff_unassigned_court.id, tx_ids)

        custody = build_custody(club=self.club_a, collector=self.staff_a)
        custody_ids = {tx.id for tx in custody["transactions"]}
        self.assertIn(self.tx_staff_assigned.id, custody_ids)
        self.assertIn(self.tx_staff_unassigned_court.id, custody_ids)

        self.client.force_authenticate(user=self.owner_a)
        overview = self.client.get(self.overview_url(self.club_a), self.range_params())
        self.assertEqual(overview.data["unsettled_transaction_count"], 2)
        self.assertEqual(overview.data["unsettled_transaction_total_amount"], "100.00")
