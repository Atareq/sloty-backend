"""
Regression tests for Reports Authorization Spine Migration (Sprint 10).

Court Usage Report is a read model. These tests prove:
1. The endpoint composes ClubScopedViewMixin + SlotyBasePermission.
2. Matrix: Admin / Owner list; Staff denied.
3. Club isolation of occupancy and financial aggregates.
4. Court filter cannot broaden to another club (HTTP 403).
5. Staff created_by filter cannot name a user outside this club.
6. Paid amounts stay on authorized bookings (not Transaction collector scope).
7. No export path exists; JSON uses the same authorized querysets.
8. Historical/report semantics (totals, clipping) are unchanged.
"""

from decimal import Decimal
from pathlib import Path

from django.urls import reverse
from rest_framework import status

from apps.bookings.models import Booking
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.roles import Role
from apps.reports.tests.test_court_usage_report import CourtUsageReportAPITestCase
from apps.reports.views import CourtUsageReportAPIView

REPO_ROOT = Path(__file__).resolve().parents[3]


class CourtUsageReportSpineCompositionTests(CourtUsageReportAPITestCase):
    def test_report_view_uses_spine_permission(self):
        self.assertTrue(issubclass(CourtUsageReportAPIView, ClubScopedViewMixin))
        self.assertEqual(
            CourtUsageReportAPIView.permission_classes, (SlotyBasePermission,)
        )
        self.assertEqual(
            CourtUsageReportAPIView.permission_view_name, "CourtUsageReportViewSet"
        )
        self.assertEqual(CourtUsageReportAPIView.action, "list")

    def test_matrix_matches_approved_report_actions(self):
        for role in (Role.ADMIN, Role.OWNER):
            self.assertTrue(is_action_allowed(role, "CourtUsageReportViewSet", "list"))
            self.assertTrue(
                is_action_allowed(role, "CourtUsageReportViewSet", "retrieve")
            )
        self.assertFalse(
            is_action_allowed(Role.STAFF, "CourtUsageReportViewSet", "list")
        )
        self.assertFalse(
            is_action_allowed(Role.STAFF, "CourtUsageReportViewSet", "retrieve")
        )

    def test_report_views_do_not_use_legacy_access_mixin(self):
        view_source = (REPO_ROOT / "apps" / "reports" / "views.py").read_text()
        service_source = (REPO_ROOT / "apps" / "reports" / "services.py").read_text()
        self.assertNotIn("ClubScopedAccessMixin", view_source)
        self.assertNotIn("CanViewClubReports", view_source)
        self.assertNotIn("ClubAccessContext", view_source)
        self.assertNotIn("ClubAccessContext", service_source)
        self.assertNotIn("scoped_report_courts_queryset", service_source)
        urls_source = (REPO_ROOT / "apps" / "reports" / "urls.py").read_text()
        self.assertNotIn("export", urls_source.lower())
        self.assertNotIn("excel", urls_source.lower())
        self.assertNotIn("csv", urls_source.lower())
        self.assertNotIn("pdf", urls_source.lower())


class CourtUsageReportAuthorizationTests(CourtUsageReportAPITestCase):
    def other_club_url(self):
        return reverse(
            "club-report-court-usage", kwargs={"club_slug": self.other_club.slug}
        )

    def test_club_isolation_does_not_inflate_aggregates(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), self.params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["booking_count"], 3)
        self.assertEqual(
            response.data["summary"]["financial"]["total_booking_value"],
            "1200.00",
        )
        court_ids = {item["court"] for item in response.data["usage_by_court"]}
        self.assertSetEqual(court_ids, {self.court.id, self.other_court.id})
        self.assertNotIn(self.cross_court.id, court_ids)
        self.assertNotEqual(
            response.data["summary"]["financial"]["total_booking_value"],
            "2199.00",
        )

    def test_other_club_member_cannot_read_this_club_report(self):
        self.client.force_authenticate(user=self.other_staff)
        response = self.client.get(self.url(), self.params())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_court_filter_cannot_escape_club_scope(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            self.url(),
            self.params(court=self.cross_court.id),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_may_filter_to_another_court_in_the_same_club(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            self.url(),
            self.params(court=self.other_court.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["booking_count"], 1)
        self.assertEqual(
            response.data["summary"]["financial"]["total_booking_value"],
            "500.00",
        )
        self.assertEqual(
            [item["court"] for item in response.data["usage_by_court"]],
            [self.other_court.id],
        )

    def test_staff_remain_forbidden_including_assigned_court(self):
        self.client.force_authenticate(user=self.staff)
        club_wide = self.client.get(self.url(), self.params())
        assigned = self.client.get(self.url(), self.params(court=self.court.id))
        self.assertEqual(club_wide.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(assigned.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_filter_cannot_name_another_club_member(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            self.url(),
            self.params(staff=self.other_staff.id),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(response, "staff"),
            "REPORT_STAFF_NOT_IN_CLUB",
        )

    def test_unauthorized_transactions_do_not_affect_paid_totals(self):
        self.create_transaction(self.cross_booking, amount=Decimal("900.00"))
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), self.params())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["summary"]["financial"]["total_paid_amount"],
            "600.00",
        )

    def test_cancelled_bookings_in_another_club_do_not_appear(self):
        self.create_booking(
            self.cross_court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            total_price=Decimal("900.00"),
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), self.params())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["booking_count"], 3)
        self.assertEqual(
            response.data["summary"]["financial"]["total_booking_value"],
            "1200.00",
        )

    def test_report_does_not_update_profile_state(self):
        profile = self.owner.profile
        modified = profile.modified
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), self.params())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        profile.refresh_from_db()
        self.assertEqual(profile.modified, modified)
