from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour
from apps.dashboard.views import (
    ClubCalendarAPIView,
    CourtAvailabilityAPIView,
    CourtUtilizationAPIView,
    DashboardOverviewAPIView,
    DashboardRevenueAPIView,
    DashboardSummaryAPIView,
)
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction


class DashboardAPITestCase(APITestCase):
    password = "test-pass-123"

    def assert_field_error(self, response, field):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn(field, response.data["field_errors"])

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_club(self, name: str, slug: str, **extra_fields) -> Club:
        data = {
            "name": name,
            "slug": slug,
            "governorate": "ASSIUT",
            "city": "ASSIUT_MARKAZ",
        }
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": Decimal("300.00"),
            "slot_duration_minutes": 60,
        }
        data.update(extra_fields)
        return Court.objects.create(**data)

    def create_membership(
        self,
        user: User,
        club: Club,
        role: str,
        court: Court | None = None,
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
        )

    def time_at(self, hour: int, minute: int = 0):
        return timezone.datetime(
            2026,
            7,
            6,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
        )

    def create_working_hours(
        self,
        court: Court,
        *,
        opens_at=time(8, 0),
        closes_at=time(12, 0),
        is_closed=False,
    ):
        for weekday in CourtWorkingHour.Weekday.values:
            CourtWorkingHour.objects.create(
                court=court,
                weekday=weekday,
                opens_at=None if is_closed else opens_at,
                closes_at=None if is_closed else closes_at,
                is_closed=is_closed,
            )

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        start_time = extra_fields.pop("start_time", self.time_at(9))
        end_time = extra_fields.pop("end_time", self.time_at(10))
        data = {
            "club": court.club,
            "court": court,
            "customer_name": "Dashboard Customer",
            "customer_phone": "+201000000001",
            "start_time": start_time,
            "end_time": end_time,
            "total_price": Decimal("300.00"),
            "status": Booking.Status.HOLD,
            "source": Booking.Source.MANUAL,
        }
        data.update(extra_fields)
        return Booking.objects.create(**data)

    def create_transaction(self, booking: Booking, **extra_fields) -> Transaction:
        created = extra_fields.pop("created", self.time_at(13))
        data = {
            "booking": booking,
            "amount": Decimal("100.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        transaction_obj = Transaction.objects.create(**data)
        Transaction.objects.filter(pk=transaction_obj.pk).update(created=created)
        transaction_obj.refresh_from_db()
        return transaction_obj

    def create_settlement(self, club: Club, **extra_fields) -> Settlement:
        created = extra_fields.pop("created", self.time_at(14))
        data = {
            "club": club,
            "period_start": self.time_at(8),
            "period_end": self.time_at(14),
            "status": Settlement.Status.PENDING,
            "total_amount": Decimal("100.00"),
            "transaction_count": 1,
        }
        data.update(extra_fields)
        settlement = Settlement.objects.create(**data)
        Settlement.objects.filter(pk=settlement.pk).update(created=created)
        settlement.refresh_from_db()
        return settlement

    def availability_url(self, club, court):
        return reverse(
            "club-court-availability",
            kwargs={"club_slug": club.slug, "court_id": court.id},
        )

    def calendar_url(self, club):
        return reverse("club-calendar", kwargs={"club_slug": club.slug})

    def overview_url(self, club):
        return reverse("club-dashboard-overview", kwargs={"club_slug": club.slug})

    def summary_url(self, club):
        return reverse("club-dashboard-summary", kwargs={"club_slug": club.slug})

    def revenue_url(self, club):
        return reverse("club-dashboard-revenue", kwargs={"club_slug": club.slug})

    def utilization_url(self, club):
        return reverse(
            "club-dashboard-court-utilization",
            kwargs={"club_slug": club.slug},
        )

    def range_params(self):
        return {
            "date_from": self.time_at(0).isoformat(),
            "date_to": self.time_at(23).isoformat(),
        }


class DashboardDataMixin:
    def setUp(self):
        self.platform_admin = self.create_user(
            "dashboard-admin",
            is_platform_admin=True,
        )
        self.owner = self.create_user("dashboard-owner")
        self.manager = self.create_user("dashboard-manager")
        self.staff = self.create_user("dashboard-staff")
        self.other_staff = self.create_user("dashboard-other-staff")
        self.club = self.create_club("Dashboard Club", "dashboard-club")
        self.other_club = self.create_club("Other Dashboard Club", "other-dashboard")
        self.court = self.create_court(self.club, "Dashboard Court 1")
        self.other_court = self.create_court(self.club, "Dashboard Court 2")
        self.cross_club_court = self.create_court(self.other_club, "Cross Court")
        self.create_working_hours(self.court)
        self.create_working_hours(self.other_court)
        self.create_working_hours(self.cross_club_court)
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.other_staff,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.cross_club_court,
        )
        self.hold = self.create_booking(
            self.court,
            customer_name="Hold Customer",
            customer_phone="+201000000101",
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.HOLD,
        )
        self.confirmed = self.create_booking(
            self.court,
            customer_name="Confirmed Customer",
            customer_phone="+201000000102",
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.CONFIRMED,
        )
        self.completed = self.create_booking(
            self.court,
            customer_name="Completed Customer",
            customer_phone="+201000000103",
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            status=Booking.Status.COMPLETED,
        )
        self.cancelled = self.create_booking(
            self.court,
            customer_name="Cancelled Customer",
            customer_phone="+201000000104",
            start_time=self.time_at(8),
            end_time=self.time_at(9),
            status=Booking.Status.CANCELLED,
        )
        self.no_show = self.create_booking(
            self.court,
            customer_name="No Show Customer",
            customer_phone="+201000000105",
            start_time=self.time_at(8),
            end_time=self.time_at(9),
            status=Booking.Status.NO_SHOW,
        )
        self.expired = self.create_booking(
            self.court,
            customer_name="Expired Customer",
            customer_phone="+201000000106",
            start_time=self.time_at(8),
            end_time=self.time_at(9),
            status=Booking.Status.EXPIRED,
        )
        self.other_court_booking = self.create_booking(
            self.other_court,
            customer_name="Other Court Customer",
            customer_phone="+201000000107",
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CONFIRMED,
        )
        self.cross_club_booking = self.create_booking(
            self.cross_club_court,
            customer_name="Cross Club Customer",
            customer_phone="+201000000108",
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CONFIRMED,
        )
        self.pending_transaction = self.create_transaction(
            self.confirmed,
            amount=Decimal("100.00"),
            created_by=self.staff,
            created=self.time_at(13),
        )
        self.settled_transaction = self.create_transaction(
            self.completed,
            amount=Decimal("300.00"),
            created_by=self.staff,
            created=self.time_at(13, 10),
        )
        self.unsettled_transaction = self.create_transaction(
            self.other_court_booking,
            amount=Decimal("80.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            payment_reference="DASH-DIGITAL-001",
            created_by=self.staff,
            created=self.time_at(13, 20),
        )
        self.cancelled_transaction = self.create_transaction(
            self.confirmed,
            amount=Decimal("60.00"),
            created=self.time_at(13, 30),
            created_by=self.manager,
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Dashboard correction",
        )
        self.pending_settlement = self.create_settlement(
            self.club,
            court=self.court,
            status=Settlement.Status.PENDING,
            total_amount=Decimal("100.00"),
            transaction_count=1,
            created=self.time_at(14),
        )
        SettlementTransaction.objects.create(
            settlement=self.pending_settlement,
            transaction=self.pending_transaction,
            amount=self.pending_transaction.amount,
        )
        self.settled_settlement = self.create_settlement(
            self.club,
            court=self.court,
            status=Settlement.Status.SETTLED,
            total_amount=Decimal("300.00"),
            transaction_count=1,
            settled_by=self.platform_admin,
            settled_at=self.time_at(15),
            created=self.time_at(14, 10),
        )
        SettlementTransaction.objects.create(
            settlement=self.settled_settlement,
            transaction=self.settled_transaction,
            amount=self.settled_transaction.amount,
        )


class AvailabilityTests(DashboardDataMixin, DashboardAPITestCase):
    def test_anonymous_rejected(self):
        response = self.client.get(
            self.availability_url(self.club, self.court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_owner_manager_and_staff_assigned_court_allowed(self):
        for user in (self.platform_admin, self.owner, self.manager, self.staff):
            self.client.force_authenticate(user=user)
            response = self.client.get(
                self.availability_url(self.club, self.court),
                {"date": "2026-07-06"},
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_rejected_for_other_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.availability_url(self.club, self.other_court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_inactive_court_returns_clear_error(self):
        self.client.force_authenticate(user=self.platform_admin)
        self.court.is_active = False
        self.court.save(update_fields=["is_active"])

        response = self.client.get(
            self.availability_url(self.club, self.court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "court")

    def test_date_required(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.availability_url(self.club, self.court))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "date")

    def test_working_hours_generate_slots_and_block_blocking_bookings(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.availability_url(self.club, self.court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["slots"]), 4)
        self.assertEqual(response.data["slots"][0]["is_available"], False)
        self.assertEqual(response.data["slots"][0]["blocking_status"], "NO_SHOW")
        self.assertEqual(response.data["slots"][1]["is_available"], False)
        self.assertEqual(response.data["slots"][1]["blocking_status"], "HOLD")
        self.assertEqual(response.data["slots"][2]["is_available"], False)
        self.assertEqual(response.data["slots"][2]["blocking_status"], "CONFIRMED")
        self.assertEqual(response.data["slots"][3]["is_available"], False)
        self.assertEqual(response.data["slots"][3]["blocking_status"], "COMPLETED")

    def test_closed_day_returns_no_slots(self):
        CourtWorkingHour.objects.filter(
            court=self.court,
            weekday=1,
        ).update(is_closed=True, opens_at=None, closes_at=None)
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.availability_url(self.club, self.court),
            {"date": "2026-07-07"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_closed"])
        self.assertEqual(response.data["slots"], [])

    def test_slot_duration_minutes_respected(self):
        short_court = self.create_court(
            self.club,
            "Thirty Minute Court",
            slot_duration_minutes=30,
        )
        CourtWorkingHour.objects.create(
            court=short_court,
            weekday=0,
            opens_at=time(8, 0),
            closes_at=time(9, 0),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.availability_url(self.club, short_court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["slots"]), 2)


class CalendarTests(DashboardDataMixin, DashboardAPITestCase):
    def test_anonymous_rejected(self):
        response = self.client.get(self.calendar_url(self.club), {"date": "2026-07-06"})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_owner_and_manager_allowed(self):
        for user in (self.platform_admin, self.owner, self.manager):
            self.client.force_authenticate(user=user)
            response = self.client.get(
                self.calendar_url(self.club),
                {"date": "2026-07-06"},
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_sees_assigned_court_items_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.calendar_url(self.club), {"date": "2026-07-06"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(
            self.other_court_booking.id,
            {item["id"] for item in response.data["items"]},
        )
        self.assertIn(
            self.confirmed.id, {item["id"] for item in response.data["items"]}
        )

    def test_date_range_court_status_and_cross_club_scoping(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.calendar_url(self.club),
            {
                **self.range_params(),
                "court": self.court.id,
                "status": Booking.Status.CONFIRMED,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["items"]}
        self.assertEqual(ids, {self.confirmed.id})
        self.assertNotIn(self.cross_club_booking.id, ids)
        item = response.data["items"][0]
        self.assertEqual(item["paid_amount"], "100.00")
        self.assertEqual(item["remaining_amount"], "200.00")
        self.assertFalse(item["is_fully_paid"])

    def test_date_filter_defaults_to_single_day(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.calendar_url(self.club), {"date": "2026-07-06"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(
            self.cross_club_booking.id,
            {item["id"] for item in response.data["items"]},
        )


class DashboardOverviewTests(DashboardDataMixin, DashboardAPITestCase):
    def test_anonymous_rejected(self):
        response = self.client.get(self.overview_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_owner_and_manager_allowed_staff_rejected(self):
        for user in (self.platform_admin, self.owner, self.manager):
            self.client.force_authenticate(user=user)
            response = self.client.get(
                self.overview_url(self.club), self.range_params()
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.overview_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_overview_metrics_and_cross_club_scoping(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.overview_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["booking_counts_by_status"]["HOLD"], 1)
        self.assertEqual(response.data["booking_counts_by_status"]["CONFIRMED"], 2)
        self.assertEqual(response.data["total_bookings"], 7)
        self.assertEqual(response.data["total_booking_value"], "2100.00")
        self.assertEqual(response.data["total_paid_amount"], "480.00")
        self.assertEqual(response.data["total_remaining_amount"], "1620.00")
        self.assertEqual(response.data["transaction_total"], "480.00")
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["unsettled_transaction_count"], 1)
        self.assertEqual(response.data["unsettled_transaction_total_amount"], "80.00")
        self.assertEqual(response.data["staff_with_unsettled_transactions_count"], 1)
        self.assertEqual(response.data["settled_amount"], "400.00")
        self.assertEqual(response.data["settled_settlement_amount"], "300.00")
        self.assertEqual(response.data["court_count"], 2)
        self.assertEqual(response.data["active_court_count"], 2)
        self.assertNotIn("unsettled_transaction_amount", response.data)
        self.assertNotIn("pending_settlement_count", response.data)
        self.assertNotIn("pending_settlement_amount", response.data)

    def test_court_filter_works(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.overview_url(self.club),
            {
                **self.range_params(),
                "court": self.court.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["court"], self.court.id)
        self.assertEqual(response.data["transaction_total"], "400.00")
        self.assertEqual(response.data["unsettled_transaction_count"], 0)
        self.assertEqual(response.data["staff_with_unsettled_transactions_count"], 0)
        self.assertEqual(response.data["court_count"], 1)


class DashboardSummaryTests(DashboardDataMixin, DashboardAPITestCase):
    def test_anonymous_rejected(self):
        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_owner_and_manager_get_all_selected_club_courts(self):
        for user, expected_role in (
            (self.platform_admin, "PLATFORM_ADMIN"),
            (self.owner, "OWNER"),
            (self.manager, "MANAGER"),
        ):
            self.client.force_authenticate(user=user)
            response = self.client.get(self.summary_url(self.club), self.range_params())

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["scope"]["role"], expected_role)
            self.assertEqual(
                set(response.data["scope"]["court_ids"]),
                {self.court.id, self.other_court.id},
            )
            self.assertTrue(response.data["scope"]["financial_visible"])

    def test_staff_gets_assigned_court_operational_summary_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["scope"]["role"], "STAFF")
        self.assertEqual(response.data["scope"]["court_ids"], [self.court.id])
        self.assertFalse(response.data["scope"]["financial_visible"])
        self.assertEqual(response.data["summary"]["total_bookings"], 6)
        self.assertEqual(response.data["summary"]["confirmed_bookings"], 1)
        self.assertIsNone(response.data["summary"]["total_booking_value"])
        self.assertIsNone(response.data["summary"]["transaction_total"])
        self.assertIsNone(
            response.data["summary"]["staff_with_unsettled_transactions_count"]
        )
        self.assertEqual(len(response.data["courts"]), 1)
        self.assertEqual(response.data["courts"][0]["court"], self.court.id)
        self.assertIsNone(response.data["courts"][0]["total_paid_amount"])

    def test_other_club_and_no_membership_users_are_rejected(self):
        no_membership_user = self.create_user("dashboard-no-membership")

        for user in (self.other_staff, no_membership_user):
            self.client.force_authenticate(user=user)
            response = self.client.get(self.summary_url(self.club), self.range_params())

            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_date_query_filters_one_day_and_default_uses_today(self):
        self.client.force_authenticate(user=self.platform_admin)

        date_response = self.client.get(
            self.summary_url(self.club),
            {"date": "2026-07-06"},
        )
        default_response = self.client.get(self.summary_url(self.club))

        self.assertEqual(date_response.status_code, status.HTTP_200_OK)
        self.assertEqual(date_response.data["summary"]["total_bookings"], 7)
        self.assertEqual(default_response.status_code, status.HTTP_200_OK)
        self.assertIn("date_from", default_response.data["period"])
        self.assertIn("date_to", default_response.data["period"])

    def test_date_range_validation(self):
        self.client.force_authenticate(user=self.platform_admin)

        reversed_response = self.client.get(
            self.summary_url(self.club),
            {
                "date_from": self.time_at(12).isoformat(),
                "date_to": self.time_at(10).isoformat(),
            },
        )
        mixed_response = self.client.get(
            self.summary_url(self.club),
            {
                "date": "2026-07-06",
                "date_from": self.time_at(0).isoformat(),
                "date_to": self.time_at(23).isoformat(),
            },
        )
        partial_response = self.client.get(
            self.summary_url(self.club),
            {"date_from": self.time_at(0).isoformat()},
        )

        self.assertEqual(reversed_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(reversed_response, "date_to")
        self.assertEqual(mixed_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(partial_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_manager_and_staff_court_filter_scope(self):
        for user in (self.owner, self.manager):
            self.client.force_authenticate(user=user)
            response = self.client.get(
                self.summary_url(self.club),
                {
                    **self.range_params(),
                    "court": self.court.id,
                },
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["scope"]["court"], self.court.id)
            self.assertEqual(response.data["scope"]["court_ids"], [self.court.id])
            self.assertEqual(len(response.data["courts"]), 1)

        self.client.force_authenticate(user=self.staff)
        assigned_response = self.client.get(
            self.summary_url(self.club),
            {
                **self.range_params(),
                "court": self.court.id,
            },
        )
        other_court_response = self.client.get(
            self.summary_url(self.club),
            {
                **self.range_params(),
                "court": self.other_court.id,
            },
        )

        self.assertEqual(assigned_response.status_code, status.HTTP_200_OK)
        self.assertEqual(assigned_response.data["scope"]["court_ids"], [self.court.id])
        self.assertEqual(other_court_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_club_court_filter_rejected(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.summary_url(self.club),
            {
                **self.range_params(),
                "court": self.cross_club_court.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_summary_booking_transaction_and_settlement_totals(self):
        other_club_settlement = self.create_settlement(
            self.other_club,
            court=self.cross_club_court,
            status=Settlement.Status.PENDING,
            total_amount=Decimal("999.00"),
            transaction_count=1,
            created=self.time_at(14),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["total_bookings"], 7)
        self.assertEqual(summary["hold_bookings"], 1)
        self.assertEqual(summary["confirmed_bookings"], 2)
        self.assertEqual(summary["completed_bookings"], 1)
        self.assertEqual(summary["cancelled_bookings"], 1)
        self.assertEqual(summary["no_show_bookings"], 1)
        self.assertEqual(summary["expired_bookings"], 1)
        self.assertEqual(summary["total_booking_value"], "2100.00")
        self.assertEqual(summary["total_paid_amount"], "480.00")
        self.assertEqual(summary["total_remaining_amount"], "1620.00")
        self.assertEqual(summary["transaction_count"], 3)
        self.assertEqual(summary["transaction_total"], "480.00")
        self.assertEqual(summary["unsettled_transaction_count"], 1)
        self.assertEqual(summary["unsettled_transaction_total_amount"], "80.00")
        self.assertEqual(summary["staff_with_unsettled_transactions_count"], 1)
        self.assertEqual(summary["settled_transaction_count"], 2)
        self.assertEqual(summary["settled_transaction_amount"], "400.00")
        self.assertEqual(summary["settled_settlement_count"], 1)
        self.assertEqual(summary["settled_settlement_amount"], "300.00")
        self.assertNotIn("unsettled_transaction_amount", summary)
        self.assertNotIn("pending_settlement_count", summary)
        self.assertNotIn("pending_settlement_amount", summary)
        self.assertNotEqual(
            summary["settled_settlement_amount"], other_club_settlement.total_amount
        )

        courts = {item["court"]: item for item in response.data["courts"]}
        self.assertEqual(courts[self.court.id]["total_bookings"], 6)
        self.assertEqual(courts[self.court.id]["transaction_total"], "400.00")
        self.assertEqual(courts[self.court.id]["unsettled_transaction_count"], 0)
        self.assertEqual(
            courts[self.court.id]["unsettled_transaction_total_amount"], "0.00"
        )
        self.assertEqual(courts[self.other_court.id]["total_bookings"], 1)
        self.assertEqual(courts[self.other_court.id]["transaction_total"], "80.00")
        self.assertEqual(courts[self.other_court.id]["unsettled_transaction_count"], 1)
        self.assertEqual(
            courts[self.other_court.id]["unsettled_transaction_total_amount"], "80.00"
        )
        court_total = sum(item["total_bookings"] for item in response.data["courts"])
        self.assertEqual(summary["total_bookings"], court_total)

    def test_summary_returns_home_page_unsettled_sections(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["context"]["club_id"], self.club.id)
        self.assertEqual(
            response.data["payment_method_totals"][Transaction.PaymentMethod.CASH],
            {"amount": "400.00", "count": 2},
        )
        self.assertEqual(
            response.data["payment_method_totals"][
                Transaction.PaymentMethod.DIGITAL_WALLET
            ],
            {"amount": "80.00", "count": 1},
        )
        self.assertEqual(len(response.data["staff_unsettled_money"]), 1)
        staff_money = response.data["staff_unsettled_money"][0]
        self.assertEqual(staff_money["collected_by"], self.staff.id)
        self.assertEqual(staff_money["court"], self.other_court.id)
        self.assertEqual(staff_money["total_unsettled_amount"], "80.00")
        self.assertEqual(staff_money["unsettled_transaction_count"], 1)
        self.assertEqual(
            staff_money["totals_by_payment_method"][
                Transaction.PaymentMethod.DIGITAL_WALLET
            ],
            "80.00",
        )

    def test_summary_filters_transaction_metrics_without_filtering_booking_counts(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.summary_url(self.club),
            {
                **self.range_params(),
                "collected_by": self.staff.id,
                "payment_method": Transaction.PaymentMethod.DIGITAL_WALLET,
                "settlement_status": "unsettled",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["total_bookings"], 7)
        self.assertEqual(summary["transaction_count"], 1)
        self.assertEqual(summary["transaction_total"], "80.00")
        self.assertEqual(summary["unsettled_transaction_count"], 1)
        self.assertEqual(summary["unsettled_transaction_total_amount"], "80.00")
        self.assertEqual(summary["staff_with_unsettled_transactions_count"], 1)
        self.assertEqual(response.data["context"]["collected_by"], self.staff.id)
        self.assertEqual(
            response.data["context"]["payment_method"],
            Transaction.PaymentMethod.DIGITAL_WALLET,
        )
        self.assertEqual(response.data["context"]["settlement_status"], "unsettled")

    def test_summary_unsettled_metrics_are_current_open_balance_not_period_only(self):
        old_unsettled = self.create_transaction(
            self.confirmed,
            amount=Decimal("20.00"),
            created_by=self.owner,
            created=self.time_at(13) - timedelta(days=2),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["transaction_count"], 3)
        self.assertEqual(summary["transaction_total"], "480.00")
        self.assertEqual(summary["unsettled_transaction_count"], 2)
        self.assertEqual(summary["unsettled_transaction_total_amount"], "100.00")
        self.assertEqual(summary["staff_with_unsettled_transactions_count"], 2)
        self.assertIn(
            old_unsettled.created_by_id,
            {item["collected_by"] for item in response.data["staff_unsettled_money"]},
        )

    def test_summary_needs_action_breakdown_excludes_completed_remaining_amount(self):
        completed_with_remaining = self.create_booking(
            self.court,
            customer_name="Completed Remaining",
            customer_phone="+201000000109",
            start_time=self.time_at(16),
            end_time=self.time_at(17),
            status=Booking.Status.COMPLETED,
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        breakdown = response.data["needs_action_breakdown"]
        self.assertEqual(summary["needs_action_count"], 3)
        self.assertEqual(breakdown["hold_waiting_payment_count"], 1)
        self.assertEqual(breakdown["overdue_confirmed_count"], 2)
        self.assertEqual(breakdown["remaining_after_slot_end_count"], 2)
        self.assertEqual(breakdown["expiring_hold_count"], 0)
        self.assertNotEqual(completed_with_remaining.status, Booking.Status.CONFIRMED)

    def test_summary_unsettled_metrics_count_distinct_users_in_scope(self):
        owner_unsettled = self.create_transaction(
            self.confirmed,
            amount=Decimal("200.00"),
            created_by=self.owner,
            created=self.time_at(13, 40),
        )
        self.create_transaction(
            self.other_court_booking,
            amount=Decimal("20.00"),
            created_by=self.staff,
            created=self.time_at(13, 45),
        )
        settled_only_user = self.create_user("dashboard-settled-only")
        settled_only_transaction = self.create_transaction(
            self.confirmed,
            amount=Decimal("400.00"),
            created_by=settled_only_user,
            created=self.time_at(13, 50),
        )
        settled_only_settlement = self.create_settlement(
            self.club,
            court=self.court,
            status=Settlement.Status.SETTLED,
            total_amount=settled_only_transaction.amount,
            transaction_count=1,
            settled_by=self.platform_admin,
            settled_at=self.time_at(15, 10),
            created=self.time_at(14, 20),
        )
        SettlementTransaction.objects.create(
            settlement=settled_only_settlement,
            transaction=settled_only_transaction,
            amount=settled_only_transaction.amount,
        )
        self.create_transaction(
            self.confirmed,
            amount=Decimal("70.00"),
            created_by=self.manager,
            created=self.time_at(13, 55),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Cancelled-only collector",
        )
        self.create_transaction(
            self.cross_club_booking,
            amount=Decimal("999.00"),
            created_by=self.other_staff,
            created=self.time_at(13, 58),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.summary_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summary = response.data["summary"]
        self.assertEqual(summary["unsettled_transaction_count"], 3)
        self.assertEqual(summary["unsettled_transaction_total_amount"], "300.00")
        self.assertEqual(summary["staff_with_unsettled_transactions_count"], 2)

        court_response = self.client.get(
            self.summary_url(self.club),
            {**self.range_params(), "court": self.court.id},
        )

        self.assertEqual(court_response.status_code, status.HTTP_200_OK)
        court_summary = court_response.data["summary"]
        self.assertEqual(court_summary["unsettled_transaction_count"], 1)
        self.assertEqual(
            court_summary["unsettled_transaction_total_amount"],
            f"{owner_unsettled.amount:.2f}",
        )
        self.assertEqual(court_summary["staff_with_unsettled_transactions_count"], 1)


class RevenueTests(DashboardDataMixin, DashboardAPITestCase):
    def test_staff_rejected(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.revenue_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_group_by_day_week_and_month(self):
        self.client.force_authenticate(user=self.platform_admin)

        for group_by in ("day", "week", "month"):
            response = self.client.get(
                self.revenue_url(self.club),
                {
                    **self.range_params(),
                    "group_by": group_by,
                },
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["group_by"], group_by)
            self.assertEqual(response.data["results"][0]["transaction_total"], "480.00")
            self.assertEqual(response.data["results"][0]["settled_amount"], "400.00")
            self.assertEqual(response.data["results"][0]["unsettled_amount"], "80.00")

    def test_court_and_payment_method_filters_work(self):
        self.client.force_authenticate(user=self.platform_admin)

        court_response = self.client.get(
            self.revenue_url(self.club),
            {
                **self.range_params(),
                "court": self.court.id,
            },
        )
        method_response = self.client.get(
            self.revenue_url(self.club),
            {
                **self.range_params(),
                "payment_method": Transaction.PaymentMethod.DIGITAL_WALLET,
            },
        )

        self.assertEqual(court_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            court_response.data["results"][0]["transaction_total"],
            "400.00",
        )
        self.assertEqual(method_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            method_response.data["results"][0]["transaction_total"],
            "80.00",
        )


class CourtUtilizationTests(DashboardDataMixin, DashboardAPITestCase):
    def test_staff_rejected(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.utilization_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_utilization_metrics(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.utilization_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = {item["court"]: item for item in response.data["results"]}
        self.assertEqual(set(results), {self.court.id, self.other_court.id})
        self.assertEqual(results[self.court.id]["booking_count"], 3)
        self.assertEqual(results[self.court.id]["booked_minutes"], 180)
        self.assertEqual(results[self.court.id]["available_minutes"], 240)
        self.assertEqual(results[self.court.id]["utilization_percentage"], "75.00")
        self.assertEqual(results[self.court.id]["transaction_total"], "400.00")
        self.assertEqual(results[self.other_court.id]["booked_minutes"], 60)
        self.assertEqual(
            results[self.other_court.id]["utilization_percentage"],
            "25.00",
        )

    def test_zero_working_hours_avoids_division_by_zero(self):
        court = self.create_court(self.club, "No Hours Court")
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.utilization_url(self.club), self.range_params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = next(
            item for item in response.data["results"] if item["court"] == court.id
        )
        self.assertEqual(result["available_minutes"], 0)
        self.assertEqual(result["utilization_percentage"], "0.00")


class DashboardSchemaRegressionTests(DashboardDataMixin, DashboardAPITestCase):
    def test_schema_and_docs_include_sprint_8_endpoints(self):
        schema_response = self.client.get(reverse("schema"))
        docs_response = self.client.get(reverse("swagger-ui"))
        schema = schema_response.content.decode()

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn("/api/v1/clubs/{club_slug}/calendar/", schema)
        self.assertIn("/api/v1/clubs/{club_slug}/dashboard/overview/", schema)
        self.assertIn("/api/v1/clubs/{club_slug}/dashboard/summary/", schema)
        self.assertIn("/api/v1/clubs/{club_slug}/dashboard/revenue/", schema)
        self.assertIn(
            "/api/v1/clubs/{club_slug}/dashboard/court-utilization/",
            schema,
        )
        self.assertIn(
            "/api/v1/clubs/{club_slug}/courts/{court_id}/availability/",
            schema,
        )

    def test_regression_boundaries(self):
        repo_root = Path(__file__).resolve().parents[2]
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)
        self.assertNotIn(
            "CourtStaffAssignment",
            (repo_root / "apps" / "courts" / "models.py").read_text(),
        )
        self.assertFalse((repo_root / "apps" / "dashboard" / "permissions.py").exists())
        self.assertNotIn(
            "ClubMembership",
            (repo_root / "apps" / "dashboard" / "views.py").read_text(),
        )
        self.assertIn(
            "get_access_context",
            (repo_root / "apps" / "dashboard" / "views.py").read_text(),
        )
        self.assertFalse(hasattr(DashboardOverviewAPIView, "post"))
        self.assertFalse(hasattr(DashboardSummaryAPIView, "post"))
        self.assertFalse(hasattr(DashboardRevenueAPIView, "post"))
        self.assertFalse(hasattr(CourtUtilizationAPIView, "post"))
        self.assertFalse(hasattr(ClubCalendarAPIView, "post"))
        self.assertFalse(hasattr(CourtAvailabilityAPIView, "post"))

    def test_seed_demo_data_supports_sprint_8_manual_testing(self):
        call_command("seed_demo_data", verbosity=0)
        call_command("seed_demo_data", verbosity=0)
        platform_admin = User.objects.get(username="platform_admin")
        booking = (
            Booking.objects.filter(
                club__slug="demo-football-club",
                status__in=Booking.BLOCKING_STATUSES,
            )
            .select_related("club", "court")
            .first()
        )
        self.client.force_authenticate(user=platform_admin)

        response = self.client.get(
            self.availability_url(booking.club, booking.court),
            {"date": booking.start_time.date().isoformat()},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            any(not slot["is_available"] for slot in response.data["slots"])
        )
        self.assertTrue(any(slot["is_available"] for slot in response.data["slots"]))
        self.assertTrue(
            Transaction.objects.filter(
                club__slug="demo-football-club",
                settlement_line__isnull=True,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(club__slug="demo-football-club").exists()
        )
