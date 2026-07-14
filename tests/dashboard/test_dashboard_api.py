from datetime import time
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
)
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction


class DashboardAPITestCase(APITestCase):
    password = "test-pass-123"

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
            created=self.time_at(13),
        )
        self.settled_transaction = self.create_transaction(
            self.completed,
            amount=Decimal("300.00"),
            created=self.time_at(13, 10),
        )
        self.unsettled_transaction = self.create_transaction(
            self.other_court_booking,
            amount=Decimal("80.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            payment_reference="DASH-DIGITAL-001",
            created=self.time_at(13, 20),
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
        self.assertIn("court", response.data)

    def test_date_required(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.availability_url(self.club, self.court))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date", response.data)

    def test_working_hours_generate_slots_and_block_only_active_bookings(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.availability_url(self.club, self.court),
            {"date": "2026-07-06"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["slots"]), 4)
        self.assertEqual(response.data["slots"][0]["is_available"], True)
        self.assertEqual(response.data["slots"][1]["is_available"], False)
        self.assertEqual(response.data["slots"][1]["blocking_status"], "HOLD")
        self.assertEqual(response.data["slots"][2]["is_available"], False)
        self.assertEqual(response.data["slots"][2]["blocking_status"], "CONFIRMED")
        self.assertEqual(response.data["slots"][3]["is_available"], True)

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
        self.assertEqual(response.data["unsettled_transaction_amount"], "80.00")
        self.assertEqual(response.data["settled_amount"], "400.00")
        self.assertEqual(response.data["pending_settlement_amount"], "100.00")
        self.assertEqual(response.data["settled_settlement_amount"], "300.00")
        self.assertEqual(response.data["court_count"], 2)
        self.assertEqual(response.data["active_court_count"], 2)

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
        self.assertEqual(response.data["court_count"], 1)


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
