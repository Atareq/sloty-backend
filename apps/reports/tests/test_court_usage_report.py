from datetime import time
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour
from apps.transactions.models import Transaction


@override_settings(ROOT_URLCONF="config.urls")
class CourtUsageReportTests(APITestCase):
    password = "test-pass-123"

    def setUp(self):
        self.admin = self.create_user("report-admin", is_platform_admin=True)
        self.owner = self.create_user("report-owner")
        self.manager = self.create_user("report-manager")
        self.staff = self.create_user("report-staff")
        self.no_membership = self.create_user("report-outsider")
        self.other_staff = self.create_user("other-report-staff")
        self.club = self.create_club("Report Club", "report-club")
        self.other_club = self.create_club("Other Report Club", "other-report-club")
        self.court = self.create_court(self.club, "Court A")
        self.other_court = self.create_court(self.club, "Court B")
        self.cross_court = self.create_court(self.other_club, "Cross Court")
        self.create_working_hours(self.court, opens_at=time(8), closes_at=time(12))
        self.create_working_hours(
            self.other_court, opens_at=time(18), closes_at=time(20)
        )
        self.create_working_hours(
            self.cross_court, opens_at=time(8), closes_at=time(12)
        )
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
            court=self.cross_court,
        )
        self.confirmed = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(8, 30),
            end_time=self.time_at(9, 30),
            created_by=self.staff,
            total_price=Decimal("300.00"),
        )
        self.completed = self.create_booking(
            self.court,
            status=Booking.Status.COMPLETED,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            created_by=self.owner,
            total_price=Decimal("400.00"),
        )
        self.no_show = self.create_booking(
            self.other_court,
            status=Booking.Status.NO_SHOW,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            created_by=self.manager,
            total_price=Decimal("500.00"),
        )
        self.hold = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            created_by=self.staff,
            total_price=Decimal("200.00"),
        )
        self.cancelled = self.create_booking(
            self.court,
            status=Booking.Status.CANCELLED,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            created_by=self.staff,
            total_price=Decimal("999.00"),
        )
        self.cross_booking = self.create_booking(
            self.cross_court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(8),
            end_time=self.time_at(9),
            total_price=Decimal("999.00"),
        )
        self.create_transaction(
            self.confirmed,
            amount=Decimal("100.00"),
            created=self.time_at(day=30),
        )
        self.create_transaction(
            self.completed,
            amount=Decimal("450.00"),
            created=self.time_at(day=8),
        )
        self.create_transaction(
            self.no_show,
            amount=Decimal("50.00"),
            created=self.time_at(day=6),
        )
        self.create_transaction(
            self.confirmed,
            amount=Decimal("60.00"),
            is_cancelled=True,
            created=self.time_at(day=6),
        )

    def create_user(self, username, **extra_fields):
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_club(self, name, slug):
        return Club.objects.create(
            name=name,
            slug=slug,
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_court(self, club, name):
        return Court.objects.create(
            club=club,
            name=name,
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )

    def create_membership(self, user, club, role, court=None):
        return ClubMembership.objects.create(
            user=user,
            club=club,
            role=role,
            court=court,
        )

    def create_working_hours(self, court, opens_at, closes_at):
        for weekday in CourtWorkingHour.Weekday.values:
            CourtWorkingHour.objects.create(
                court=court,
                weekday=weekday,
                opens_at=opens_at,
                closes_at=closes_at,
            )

    def time_at(self, hour=0, minute=0, day=6):
        return timezone.datetime(
            2026,
            7,
            day,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
        )

    def create_booking(self, court, **extra_fields):
        data = {
            "club": court.club,
            "court": court,
            "customer_name": "Report Customer",
            "customer_phone": "+201000000501",
            "start_time": self.time_at(8),
            "end_time": self.time_at(9),
            "total_price": Decimal("300.00"),
            "status": Booking.Status.CONFIRMED,
            "source": Booking.Source.MANUAL,
        }
        data.update(extra_fields)
        return Booking.objects.create(**data)

    def create_transaction(self, booking, **extra_fields):
        created = extra_fields.pop("created", self.time_at(13))
        data = {
            "booking": booking,
            "amount": Decimal("100.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        transaction = Transaction.objects.create(**data)
        Transaction.objects.filter(pk=transaction.pk).update(created=created)
        transaction.refresh_from_db()
        return transaction

    def url(self):
        return reverse("club-report-court-usage", kwargs={"club_slug": self.club.slug})

    def params(self, **extra):
        data = {"date_from": "2026-07-06", "date_to": "2026-07-06"}
        data.update(extra)
        return data

    def field_error_code(self, response, field):
        return response.data["field_errors"][field][0]["code"]

    def test_permission_roles(self):
        response = self.client.get(self.url(), self.params())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        for user in (self.admin, self.owner, self.manager):
            self.client.force_authenticate(user=user)
            response = self.client.get(self.url(), self.params())
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        for user in (self.staff, self.no_membership):
            self.client.force_authenticate(user=user)
            response = self.client.get(self.url(), self.params())
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_default_usage_financials_and_no_show_behavior(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url(), self.params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["booking_count"], 3)
        self.assertEqual(response.data["summary"]["occupied_minutes"], 180)
        self.assertEqual(response.data["summary"]["available_minutes"], 360)
        self.assertEqual(response.data["summary"]["utilization_percentage"], "50.00")
        self.assertEqual(response.data["summary"]["status_counts"]["NO_SHOW"], 1)
        self.assertEqual(
            response.data["summary"]["financial"]["total_booking_value"],
            "1200.00",
        )
        self.assertEqual(
            response.data["summary"]["financial"]["total_paid_amount"],
            "600.00",
        )
        self.assertEqual(
            response.data["summary"]["financial"]["total_remaining_amount"],
            "650.00",
        )

    def test_filters_and_partial_overlap_clipping(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.url(),
            self.params(
                court=self.court.id,
                period="custom",
                hour_from="09:00",
                hour_to="10:00",
                staff=self.staff.id,
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["context"]["court"], self.court.id)
        self.assertEqual(response.data["context"]["staff"], self.staff.id)
        self.assertEqual(response.data["summary"]["booking_count"], 1)
        self.assertEqual(response.data["summary"]["occupied_minutes"], 30)
        self.assertEqual(response.data["summary"]["available_minutes"], 60)
        self.assertEqual(response.data["usage_by_period"][0]["period"], "custom")

    def test_hold_can_be_inspected_explicitly_and_cancelled_expired_rejected(self):
        self.client.force_authenticate(user=self.owner)

        hold_response = self.client.get(self.url(), self.params(status="HOLD"))
        cancelled_response = self.client.get(
            self.url(),
            self.params(status="CANCELLED"),
        )
        expired_response = self.client.get(
            self.url(),
            self.params(status="EXPIRED"),
        )

        self.assertEqual(hold_response.status_code, status.HTTP_200_OK)
        self.assertEqual(hold_response.data["summary"]["booking_count"], 1)
        self.assertEqual(cancelled_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(cancelled_response, "status"),
            "INVALID_COURT_USAGE_STATUS",
        )
        self.assertEqual(expired_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(expired_response, "status"),
            "INVALID_COURT_USAGE_STATUS",
        )

    def test_low_demand_includes_zero_demand_working_hour_bucket(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url(), self.params(court=self.court.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        low_demand_counts = {
            item["booking_count"] for item in response.data["low_demand_hours"]
        }
        self.assertIn(0, low_demand_counts)

    def test_validation_error_codes_are_stable(self):
        self.client.force_authenticate(user=self.owner)

        invalid_range = self.client.get(
            self.url(),
            {"date_from": "2026-07-07", "date_to": "2026-07-06"},
        )
        too_large = self.client.get(
            self.url(),
            {"date_from": "2026-07-01", "date_to": "2026-08-01"},
        )
        missing_custom_hours = self.client.get(
            self.url(),
            self.params(period="custom", hour_from="09:00"),
        )
        invalid_custom_hours = self.client.get(
            self.url(),
            self.params(period="custom", hour_from="10:00", hour_to="09:00"),
        )
        invalid_staff = self.client.get(
            self.url(),
            self.params(staff=self.other_staff.id),
        )

        self.assertEqual(invalid_range.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(invalid_range, "date_to"),
            "REPORT_DATE_RANGE_INVALID",
        )
        self.assertEqual(too_large.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(too_large, "date_to"),
            "REPORT_DATE_RANGE_TOO_LARGE",
        )
        self.assertEqual(
            missing_custom_hours.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.field_error_code(missing_custom_hours, "hour_from"),
            "CUSTOM_REPORT_HOURS_REQUIRED",
        )
        self.assertEqual(
            invalid_custom_hours.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.field_error_code(invalid_custom_hours, "hour_to"),
            "INVALID_CUSTOM_REPORT_HOURS",
        )
        self.assertEqual(invalid_staff.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.field_error_code(invalid_staff, "staff"),
            "REPORT_STAFF_NOT_IN_CLUB",
        )

    def test_transaction_created_date_does_not_filter_financials(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url(), self.params(court=self.court.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["summary"]["financial"]["total_paid_amount"],
            "550.00",
        )

    def test_query_count_stays_bounded_for_report_rows(self):
        self.client.force_authenticate(user=self.owner)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url(), self.params())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 8)
