from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.bookings.models import Booking
from tests.bookings.test_booking_api import BookingAPITestCase

CAIRO = ZoneInfo("Africa/Cairo")


class EgyptTimezoneBoundaryTests(BookingAPITestCase):
    def cairo_at(self, hour, minute=0, day=20, month=5, year=2026):
        return timezone.datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=CAIRO,
        )

    def setUp(self):
        self.platform_admin = self.create_platform_admin("cairo-admin")
        self.club = self.create_club("Cairo Club", slug="cairo-club")
        self.court = self.create_court(self.club, "Cairo Court")
        self.client.force_authenticate(user=self.platform_admin)

    def get_slots(self, **params):
        data = {"court": self.court.id, "date": "2026-05-20"}
        data.update(params)
        return self.client.get(self.booking_slots_url(self.club), data)

    def slot_by_hour(self, response, hour, date=None):
        expected_datetime = self.time_at(hour)
        if date is not None:
            expected_datetime = timezone.datetime(
                date.year,
                date.month,
                date.day,
                hour,
                tzinfo=timezone.get_current_timezone(),
            )
        start_time_prefix = expected_datetime.strftime("%Y-%m-%dT%H:%M:%S")
        return next(
            slot
            for slot in response.data["slots"]
            if (
                slot["start_time"] == expected_datetime
                or (
                    isinstance(slot["start_time"], str)
                    and slot["start_time"].startswith(start_time_prefix)
                )
            )
        )

    def test_django_business_timezone_is_africa_cairo(self):
        self.assertEqual(settings.TIME_ZONE, "Africa/Cairo")
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(str(timezone.get_current_timezone()), "Africa/Cairo")

    def test_booking_date_filter_uses_cairo_calendar_day(self):
        early = self.create_booking(
            self.court,
            start_time=self.cairo_at(0, 30),
            end_time=self.cairo_at(1, 30),
            customer_phone="+201000008001",
        )
        after_one = self.create_booking(
            self.court,
            start_time=self.cairo_at(1, 0),
            end_time=self.cairo_at(2, 0),
            customer_phone="+201000008002",
        )
        late = self.create_booking(
            self.court,
            start_time=self.cairo_at(23, 0),
            end_time=self.cairo_at(23, 30),
            customer_phone="+201000008003",
        )
        previous_night = self.create_booking(
            self.court,
            start_time=self.cairo_at(23, 30, day=19),
            end_time=self.cairo_at(0, 30, day=20),
            customer_phone="+201000008004",
        )
        next_morning = self.create_booking(
            self.court,
            start_time=self.cairo_at(0, 30, day=21),
            end_time=self.cairo_at(1, 30, day=21),
            customer_phone="+201000008005",
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self.list_ids(response)
        self.assertIn(early.id, ids)
        self.assertIn(after_one.id, ids)
        self.assertIn(late.id, ids)
        self.assertIn(previous_night.id, ids)
        self.assertNotIn(next_morning.id, ids)
        self.assertEqual(timezone.localtime(early.start_time).date(), date(2026, 5, 20))
        self.assertEqual(timezone.localtime(late.start_time).date(), date(2026, 5, 20))

    def test_schedule_and_virtual_recurrence_use_cairo_weekdays(self):
        self.create_booking(
            self.court,
            start_time=self.cairo_at(22, 0),
            end_time=self.cairo_at(23, 0),
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            customer_name="Cairo Recurring",
            customer_phone="+201000008010",
        )

        same_day = self.get_slots(date="2026-05-20")
        next_week = self.get_slots(date="2026-05-27")

        self.assertEqual(same_day.status_code, status.HTTP_200_OK)
        self.assertEqual(next_week.status_code, status.HTTP_200_OK)
        occupied = self.slot_by_hour(same_day, 22, date=date(2026, 5, 20))
        reserved = self.slot_by_hour(next_week, 22, date=date(2026, 5, 27))
        self.assertEqual(occupied["slot_status"], Booking.Status.CONFIRMED)
        self.assertEqual(reserved["slot_status"], "RECURRING_RESERVED")
        self.assertIsNone(reserved["booking"])
        self.assertEqual(
            reserved["recurring_context"]["customer_name"],
            "Cairo Recurring",
        )

    def test_dashboard_calendar_day_includes_cairo_midnight_booking(self):
        booking = self.create_booking(
            self.court,
            start_time=self.cairo_at(0, 30),
            end_time=self.cairo_at(1, 30),
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000008020",
        )
        later_day = self.create_booking(
            self.court,
            start_time=self.cairo_at(0, 30, day=21),
            end_time=self.cairo_at(1, 30, day=21),
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000008021",
        )

        response = self.client.get(
            reverse("club-calendar", kwargs={"club_slug": self.club.slug}),
            {"date": "2026-05-20"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["items"]}
        self.assertIn(booking.id, ids)
        self.assertNotIn(later_day.id, ids)

    def test_report_usage_by_day_groups_by_cairo_start_date(self):
        self.create_booking(
            self.court,
            start_time=self.cairo_at(22, 0),
            end_time=self.cairo_at(23, 0),
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000008030",
        )

        response = self.client.get(
            reverse(
                "club-report-court-usage",
                kwargs={"club_slug": self.club.slug},
            ),
            {"date_from": "2026-05-20", "date_to": "2026-05-20", "status": "CONFIRMED"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_day = {item["date"]: item for item in response.data["usage_by_day"]}
        self.assertIn("2026-05-20", by_day)
        self.assertGreaterEqual(by_day["2026-05-20"]["booking_count"], 1)
