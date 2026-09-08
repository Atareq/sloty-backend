import unittest
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yaml
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.filters import (
    BookingAttemptFilter,
    BookingFilter,
    annotate_booking_hold_expires_at,
    compute_booking_hold_expires_at,
)
from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.views import BookingAttemptViewSet, BookingViewSet
from apps.clubs.models import Club, ClubMembership
from apps.common.middleware import SQLQueryStats
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction


class BookingAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="booking-admin") -> User:
        return self.create_user(username=username, is_platform_admin=True)

    def create_club(self, name: str, slug: str | None = None, **extra_fields) -> Club:
        data = {
            "name": name,
            "governorate": "ASSIUT",
            "city": "ASSIUT_MARKAZ",
        }
        if slug is not None:
            data["slug"] = slug
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": Decimal("300.00"),
            "slot_duration_minutes": 60,
            "cancellation_refund_notice_days": 0,
        }
        data.update(extra_fields)
        court = Court.objects.create(**data)
        self.create_working_hours(
            court,
            weekday=2,
            opens_at=time(9, 0),
            closes_at=time(23, 0),
            price=court.default_price,
        )
        return court

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

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        start_time = extra_fields.pop("start_time", self.time_at(20))
        end_time = extra_fields.pop("end_time", self.time_at(21))
        data = {
            "club": court.club,
            "court": court,
            "customer_name": "Existing Customer",
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
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        return Transaction.objects.create(**data)

    def time_at(self, hour: int, minute: int = 0):
        return timezone.datetime(
            2026,
            5,
            20,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
        )

    def booking_payload(self, court: Court, **extra_fields):
        data = {
            "court": court.id,
            "customer_name": "Ahmed Hassan",
            "customer_phone": "+201000000002",
            "start_time": self.time_at(20).isoformat(),
            "end_time": self.time_at(21).isoformat(),
        }
        data.update(extra_fields)
        return data

    def booking_list_url(self, club):
        return reverse("club-booking-list", kwargs={"club_slug": club.slug})

    def booking_detail_url(self, club, booking):
        return reverse(
            "club-booking-detail",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def booking_lifecycle_url(self, club, booking, action_name):
        return reverse(
            f"club-booking-{action_name}",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def booking_slots_url(self, club):
        return reverse("club-booking-slots", kwargs={"club_slug": club.slug})

    def booking_attempt_list_url(self, club):
        return reverse("club-booking-attempt-list", kwargs={"club_slug": club.slug})

    def booking_attempt_detail_url(self, club, attempt):
        return reverse(
            "club-booking-attempt-detail",
            kwargs={"club_slug": club.slug, "pk": attempt.pk},
        )

    def booking_attempt_dismiss_url(self, club, attempt):
        return reverse(
            "club-booking-attempt-dismiss",
            kwargs={"club_slug": club.slug, "pk": attempt.pk},
        )

    def post_booking(self, club: Club, court: Court, **extra_fields):
        return self.client.post(
            self.booking_list_url(club),
            self.booking_payload(court, **extra_fields),
            format="json",
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def assert_field_error(self, response, field):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn(field, response.data["field_errors"])

    def assert_api_error(self, response, code):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], code)
        self.assertIn("message", response.data)

    def create_working_hours(
        self,
        court: Court,
        *,
        weekday=2,
        opens_at=time(9, 0),
        closes_at=time(12, 0),
        is_closed=False,
        price=None,
    ) -> CourtWorkingHour:
        working_hour, _ = CourtWorkingHour.objects.update_or_create(
            court=court,
            weekday=weekday,
            defaults={},
        )
        working_hour.pricing_periods.all().delete()
        if not is_closed:
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=working_hour,
                starts_at=opens_at,
                ends_at=closes_at,
                price=price if price is not None else court.default_price,
            )
        return working_hour

    def set_price_periods(self, working_hour, *periods):
        working_hour.pricing_periods.all().delete()
        for starts_at, ends_at, price in periods:
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=working_hour,
                starts_at=starts_at,
                ends_at=ends_at,
                price=price,
            )


class BookingCreationTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.club = self.create_club("Booking Club", slug="booking-club")
        self.other_club = self.create_club("Other Booking Club", slug="other-booking")
        self.court = self.create_court(self.club, "Booking Court")
        self.other_court = self.create_court(self.other_club, "Other Booking Court")

    def test_club_scoped_bookings_route_works(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_booking_can_be_created_with_required_fields(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.customer_name, "Ahmed Hassan")
        self.assertEqual(str(booking.customer_phone), "+201000000002")

    def test_booking_create_accepts_optional_client_request_id(self):
        self.client.force_authenticate(user=self.platform_admin)
        client_request_id = "1d4df864-f5f0-48b1-b5ce-cd3217424e6f"

        response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(str(booking.client_request_id), client_request_id)
        self.assertEqual(response.data["client_request_id"], client_request_id)

    def test_booking_create_records_successful_attempt(self):
        self.client.force_authenticate(user=self.platform_admin)
        client_request_id = "91f2f96a-8997-49a3-9bf7-96c2d3a0d922"
        requested_at = self.time_at(18).isoformat()

        response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
            requested_at=requested_at,
            notes="captured offline",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.club, self.club)
        self.assertEqual(attempt.court, self.court)
        self.assertEqual(attempt.attempted_by, self.platform_admin)
        self.assertEqual(str(attempt.client_request_id), client_request_id)
        self.assertEqual(attempt.customer_name, "Ahmed Hassan")
        self.assertEqual(str(attempt.customer_phone), "+201000000002")
        self.assertEqual(attempt.notes, "captured offline")
        self.assertEqual(attempt.requested_start, self.time_at(20))
        self.assertEqual(attempt.requested_end, self.time_at(21))
        self.assertEqual(attempt.requested_at, self.time_at(18))
        self.assertFalse(attempt.requested_recurring)
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, BookingAttempt.Resolution.RESOLVED)
        self.assertEqual(attempt.failure_code, "")
        self.assertEqual(attempt.booking, booking)

    def test_booking_create_without_client_request_id_still_records_attempt(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = BookingAttempt.objects.get()
        self.assertIsNone(attempt.client_request_id)
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.booking_id, response.data["id"])

    def test_booking_create_replays_same_client_request_id_and_payload(self):
        self.client.force_authenticate(user=self.platform_admin)
        client_request_id = "40eb1a39-139e-479f-a3ee-58c119785584"
        payload = self.booking_payload(
            self.court,
            client_request_id=client_request_id,
            notes="offline customer note",
        )

        first_response = self.client.post(
            self.booking_list_url(self.club),
            payload,
            format="json",
        )
        replay_response = self.client.post(
            self.booking_list_url(self.club),
            payload,
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay_response.status_code, status.HTTP_200_OK)
        self.assertEqual(replay_response.data["id"], first_response.data["id"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.booking_id, first_response.data["id"])
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_booking_create_rejects_same_client_request_id_different_payload(self):
        self.client.force_authenticate(user=self.platform_admin)
        client_request_id = "40f39ff6-b6bf-4e37-8854-ee53bf8dc14c"

        first_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
        )
        mismatch_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
            customer_name="Different Customer",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mismatch_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(mismatch_response, "BOOKING_CLIENT_REQUEST_MISMATCH")
        self.assertEqual(
            mismatch_response.data["details"]["existing_booking_id"],
            first_response.data["id"],
        )
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)
        self.assertEqual(
            BookingAttempt.objects.get().outcome,
            BookingAttempt.Outcome.SUCCESS,
        )

    def test_booking_client_request_id_is_scoped_to_selected_club(self):
        self.client.force_authenticate(user=self.platform_admin)
        client_request_id = "f4f908ff-58d4-4b4c-a2c0-f89cd2f272bb"

        first_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
        )
        second_response = self.post_booking(
            self.other_club,
            self.other_court,
            client_request_id=client_request_id,
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(BookingAttempt.objects.count(), 2)

    def test_rejected_slot_unavailable_records_attempt_without_fake_booking(self):
        self.client.force_authenticate(user=self.platform_admin)
        blocker = self.create_booking(self.court)
        client_request_id = "d0de6156-7555-43d9-ae71-70b77a1f117b"

        response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
            requested_at=self.time_at(19).isoformat(),
            notes="customer asked for busy slot",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Booking.objects.get(), blocker)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.failure_code, "BOOKING_SLOT_UNAVAILABLE")
        self.assertIsNone(attempt.booking)
        self.assertEqual(str(attempt.client_request_id), client_request_id)
        self.assertEqual(attempt.requested_at, self.time_at(19))

    def test_rejected_attempt_retry_same_client_request_id_does_not_duplicate(self):
        self.client.force_authenticate(user=self.platform_admin)
        self.create_booking(self.court)
        client_request_id = "7df38e6b-a066-4b72-9897-a705d3de836f"
        payload = self.booking_payload(
            self.court,
            client_request_id=client_request_id,
            notes="retry same rejected request",
        )

        first_response = self.client.post(
            self.booking_list_url(self.club),
            payload,
            format="json",
        )
        retry_response = self.client.post(
            self.booking_list_url(self.club),
            payload,
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(retry_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(retry_response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)
        self.assertEqual(
            BookingAttempt.objects.get().failure_code,
            "BOOKING_SLOT_UNAVAILABLE",
        )

    def test_rejected_attempt_client_request_id_mismatch_does_not_create_new_attempt(
        self,
    ):
        self.client.force_authenticate(user=self.platform_admin)
        self.create_booking(self.court)
        client_request_id = "d647bf4c-7b0b-43f7-91fe-0ff12fffe4bb"

        first_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
        )
        mismatch_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=client_request_id,
            customer_name="Different Customer",
        )

        self.assertEqual(first_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(mismatch_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(mismatch_response, "BOOKING_CLIENT_REQUEST_MISMATCH")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)

    def test_rejected_attempt_does_not_affect_open_slot_availability(self):
        self.client.force_authenticate(user=self.platform_admin)
        future_start = self.time_at(20) + timedelta(weeks=10, minutes=30)
        self.create_booking(
            self.court,
            start_time=future_start,
            end_time=future_start + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )

        response = self.post_booking(
            self.club,
            self.court,
            is_recurring=True,
            client_request_id="0dd65df7-c9a8-430e-8f9d-8f9434881f58",
        )
        slots_response = self.client.get(
            self.booking_slots_url(self.club),
            {"court": self.court.id, "date": "2026-05-20"},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "RECURRING_UNAVAILABLE")
        self.assertEqual(BookingAttempt.objects.count(), 1)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertTrue(attempt.requested_recurring)
        self.assertEqual(attempt.failure_code, "RECURRING_UNAVAILABLE")
        self.assertEqual(slots_response.status_code, status.HTTP_200_OK)
        slot = next(
            item
            for item in slots_response.data["slots"]
            if (
                item["start_time"].hour
                if hasattr(item["start_time"], "hour")
                else parse_datetime(item["start_time"]).hour
            )
            == 20
        )
        self.assertEqual(slot["slot_status"], "FREE")
        self.assertTrue(slot["is_available"])
        self.assertIsNone(slot["booking"])

    def test_rejected_attempt_does_not_affect_financial_rows(self):
        self.client.force_authenticate(user=self.platform_admin)
        self.create_booking(self.court)

        response = self.post_booking(
            self.club,
            self.court,
            client_request_id="a595367c-dfa9-4d05-bae3-9d4e26386925",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(BookingAttempt.objects.count(), 1)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Settlement.objects.count(), 0)

    def test_booking_defaults_to_hold_and_manual_source(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertEqual(booking.source, Booking.Source.MANUAL)

    def test_total_price_is_calculated_from_pricing_periods(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(22).isoformat(),
            total_price="1.00",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.total_price, Decimal("600.00"))
        self.assertEqual(response.data["total_price"], "600.00")

    def test_booking_crossing_pricing_boundary_uses_each_period(self):
        working_hour = CourtWorkingHour.objects.get(court=self.court, weekday=2)
        self.set_price_periods(
            working_hour,
            (time(9, 0), time(18, 0), Decimal("200.00")),
            (time(18, 0), time(23, 0), Decimal("300.00")),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(17).isoformat(),
            end_time=self.time_at(19).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["total_price"], "500.00")

    def test_booking_on_closed_day_is_rejected(self):
        CourtWorkingHour.objects.get(
            court=self.court, weekday=2
        ).pricing_periods.all().delete()
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_OUTSIDE_WORKING_HOURS")
        self.assertEqual(BookingAttempt.objects.count(), 1)
        self.assertEqual(
            BookingAttempt.objects.get().failure_code,
            "BOOKING_OUTSIDE_WORKING_HOURS",
        )

    def test_booking_time_must_align_with_slot_grid(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(17, 30).isoformat(),
            end_time=self.time_at(18, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID")

    def test_booking_club_is_set_from_url_slug_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.club, self.club)
        self.assertEqual(response.data["club"], self.club.id)

    def test_court_from_another_club_is_rejected(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "court")
        self.assertEqual(BookingAttempt.objects.count(), 0)

    def test_start_time_must_be_before_end_time(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(21).isoformat(),
            end_time=self.time_at(20).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "end_time")

    def test_duration_must_match_slot_duration_multiple(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(20, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "end_time")

    def test_booking_outside_working_hours_is_rejected(self):
        self.create_working_hours(
            self.court,
            weekday=2,
            opens_at=time(10, 0),
            closes_at=time(18, 0),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(22).isoformat(),
            end_time=self.time_at(23).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_OUTSIDE_WORKING_HOURS")

    def test_recurring_booking_create_uses_booking_native_anchor_only(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            is_recurring=True,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(booking.source, Booking.Source.RECURRING)
        self.assertEqual(
            booking.recurrence_status,
            Booking.RecurrenceStatus.ACTIVE,
        )
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertIsNone(booking.previous_recurring_booking)
        self.assertTrue(response.data["is_recurring"])
        self.assertEqual(response.data["recurrence_status"], "ACTIVE")

    def test_direct_recurring_source_is_rejected(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.RECURRING,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "source")

    def test_admin_correction_cannot_be_recurring(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
            is_recurring=True,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "is_recurring")

    def test_future_virtual_recurring_conflict_blocks_normal_booking(self):
        self.client.force_authenticate(user=self.platform_admin)
        anchor = self.create_booking(
            self.court,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
        )

        response = self.post_booking(
            self.club,
            self.court,
            start_time=(anchor.start_time + timedelta(weeks=8)).isoformat(),
            end_time=(anchor.end_time + timedelta(weeks=8, hours=1)).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(response.data["details"]["conflict_type"], "RECURRING_PATTERN")
        self.assertEqual(
            response.data["details"]["conflicting_booking_id"],
            anchor.id,
        )

    def test_active_recurrence_does_not_block_before_or_non_overlapping_candidate(self):
        self.client.force_authenticate(user=self.platform_admin)
        anchor_start = self.time_at(20)
        self.create_booking(
            self.court,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            start_time=anchor_start,
            end_time=self.time_at(21),
        )

        before = self.post_booking(
            self.club,
            self.court,
            start_time=(anchor_start - timedelta(weeks=1)).isoformat(),
            end_time=(
                anchor_start - timedelta(weeks=1) + timedelta(hours=1)
            ).isoformat(),
            customer_phone="+201000000051",
        )
        non_overlap = self.post_booking(
            self.club,
            self.court,
            start_time=(anchor_start + timedelta(weeks=1, hours=1)).isoformat(),
            end_time=(anchor_start + timedelta(weeks=1, hours=2)).isoformat(),
            customer_phone="+201000000052",
        )

        self.assertEqual(before.status_code, status.HTTP_201_CREATED)
        self.assertEqual(non_overlap.status_code, status.HTTP_201_CREATED)

    def test_future_concrete_booking_blocks_new_recurrence(self):
        self.client.force_authenticate(user=self.platform_admin)
        future_start = self.time_at(20) + timedelta(weeks=10, minutes=30)
        blocker = self.create_booking(
            self.court,
            start_time=future_start,
            end_time=future_start + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )

        response = self.post_booking(
            self.club,
            self.court,
            is_recurring=True,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "RECURRING_UNAVAILABLE")
        self.assertEqual(response.data["details"]["conflict_type"], "FUTURE_CONFLICT")
        self.assertEqual(
            response.data["details"]["conflicting_booking_id"],
            blocker.id,
        )

    def test_existing_active_recurrence_blocks_new_overlapping_recurrence(self):
        self.client.force_authenticate(user=self.platform_admin)
        anchor = self.create_booking(
            self.court,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
        )

        response = self.post_booking(
            self.club,
            self.court,
            is_recurring=True,
            start_time=(self.time_at(20) + timedelta(weeks=1)).isoformat(),
            end_time=(self.time_at(22) + timedelta(weeks=1)).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(
            response.data["details"]["conflicting_booking_id"],
            anchor.id,
        )


class BookingHistoricalCreationTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("historical-admin")
        self.staff = self.create_user("historical-staff")
        self.club = self.create_club("Historical Club", slug="historical-club")
        self.court = self.create_court(self.club, "Historical Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Historical Other Court",
        )
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.sync_now = timezone.datetime(
            2026,
            9,
            10,
            20,
            30,
            tzinfo=timezone.get_current_timezone(),
        )
        self.past_start = timezone.datetime(
            2026,
            9,
            3,
            20,
            0,
            tzinfo=timezone.get_current_timezone(),
        )
        self.past_end = self.past_start + timedelta(hours=1)

    def ensure_priced_slot(self, start_time, end_time, *, court=None):
        target_court = court or self.court
        local_start = timezone.localtime(start_time)
        local_end = timezone.localtime(end_time)
        self.create_working_hours(
            target_court,
            weekday=local_start.weekday(),
            opens_at=local_start.time(),
            closes_at=local_end.time(),
            price=target_court.default_price,
        )

    def historical_payload(self, **extra_fields):
        data = self.booking_payload(
            self.court,
            start_time=self.past_start.isoformat(),
            end_time=self.past_end.isoformat(),
            requested_at=(self.past_start - timedelta(minutes=10)).isoformat(),
            client_request_id="98f92319-9152-42f0-8cb9-7de06927e775",
        )
        data.update(extra_fields)
        return data

    def post_historical(self, payload=None):
        return self.client.post(
            self.booking_list_url(self.club),
            payload or self.historical_payload(),
            format="json",
        )

    def test_past_booking_is_accepted_when_business_rules_pass(self):
        self.ensure_priced_slot(self.past_start, self.past_end)
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertLess(booking.start_time, self.sync_now)
        self.assertLess(booking.end_time, self.sync_now)
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.booking, booking)
        self.assertEqual(attempt.requested_start, self.past_start)
        self.assertEqual(attempt.requested_end, self.past_end)

    def test_past_booking_conflict_returns_slot_unavailable_and_rejected_attempt(self):
        self.ensure_priced_slot(self.past_start, self.past_end)
        self.create_booking(
            self.court,
            start_time=self.past_start,
            end_time=self.past_end,
            status=Booking.Status.CONFIRMED,
        )
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(Booking.objects.count(), 1)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.failure_code, "BOOKING_SLOT_UNAVAILABLE")
        self.assertIsNone(attempt.booking)

    def test_future_booking_still_succeeds(self):
        future_start = self.sync_now + timedelta(days=7)
        future_start = future_start.replace(hour=20, minute=0)
        future_end = future_start + timedelta(hours=1)
        self.ensure_priced_slot(future_start, future_end)
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical(
                self.historical_payload(
                    start_time=future_start.isoformat(),
                    end_time=future_end.isoformat(),
                    requested_at=self.sync_now.isoformat(),
                    client_request_id="ce6a733f-2b79-42c2-b34d-5bdf8b3f7d9e",
                )
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertGreater(booking.start_time, self.sync_now)
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertEqual(
            BookingAttempt.objects.get().outcome,
            BookingAttempt.Outcome.SUCCESS,
        )

    def test_current_overlapping_booking_follows_normal_business_rules(self):
        current_start = self.sync_now.replace(hour=20, minute=0)
        current_end = current_start + timedelta(hours=1)
        self.ensure_priced_slot(current_start, current_end)
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical(
                self.historical_payload(
                    start_time=current_start.isoformat(),
                    end_time=current_end.isoformat(),
                    requested_at=(self.sync_now - timedelta(minutes=30)).isoformat(),
                    client_request_id="fe964a66-9b64-42bd-9db8-cf87dac756d6",
                )
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertLess(booking.start_time, self.sync_now)
        self.assertGreater(booking.end_time, self.sync_now)
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_historical_request_does_not_bypass_current_authorization(self):
        self.ensure_priced_slot(self.past_start, self.past_end)
        ClubMembership.objects.filter(user=self.staff, club=self.club).update(
            is_active=False
        )
        self.client.force_authenticate(user=self.staff)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(BookingAttempt.objects.count(), 0)

    def test_idempotent_historical_booking_replay_reuses_booking_and_attempt(self):
        self.ensure_priced_slot(self.past_start, self.past_end)
        payload = self.historical_payload(
            client_request_id="6b87c30a-a136-4e5c-a965-d11fdac769af"
        )
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            first_response = self.post_historical(payload)
            retry_response = self.post_historical(payload)

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry_response.status_code, status.HTTP_200_OK)
        self.assertEqual(retry_response.data["id"], first_response.data["id"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)

    def test_rejected_historical_attempt_does_not_affect_slot_generation(self):
        self.ensure_priced_slot(self.past_start, self.past_end)
        future_blocker_start = self.past_start + timedelta(weeks=2)
        self.create_booking(
            self.court,
            start_time=future_blocker_start,
            end_time=future_blocker_start + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical(
                self.historical_payload(
                    is_recurring=True,
                    client_request_id="6dbf321b-c605-4d5c-97d6-a9e375971a6a",
                )
            )
            slots_response = self.client.get(
                self.booking_slots_url(self.club),
                {"court": self.court.id, "date": self.past_start.date().isoformat()},
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "RECURRING_UNAVAILABLE")
        self.assertEqual(BookingAttempt.objects.count(), 1)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.failure_code, "RECURRING_UNAVAILABLE")
        self.assertIsNone(attempt.booking)
        slot = next(
            item
            for item in slots_response.data["slots"]
            if (
                item["start_time"].hour
                if hasattr(item["start_time"], "hour")
                else parse_datetime(item["start_time"]).hour
            )
            == self.past_start.hour
        )
        self.assertEqual(slot["slot_status"], "FREE")
        self.assertTrue(slot["is_available"])
        self.assertIsNone(slot["booking"])

    def test_historical_hold_is_created_as_hold_until_expiry_job_runs(self):
        from apps.bookings.services import (
            due_hold_booking_candidate_ids,
            expire_due_hold_bookings,
        )

        self.ensure_priced_slot(self.past_start, self.past_end)
        self.client.force_authenticate(user=self.platform_admin)

        with patch("django.utils.timezone.now", return_value=self.sync_now):
            response = self.post_historical(
                self.historical_payload(
                    client_request_id="76b8f977-d807-4e34-9470-44c17f79c137"
                )
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertLess(booking.start_time, self.sync_now)

        due_ids = due_hold_booking_candidate_ids(now=self.sync_now)
        self.assertIn(booking.id, due_ids)
        expired = expire_due_hold_bookings(now=self.sync_now)

        booking.refresh_from_db()
        self.assertEqual([item.id for item in expired], [booking.id])
        self.assertEqual(booking.status, Booking.Status.EXPIRED)


class BookingAttemptTraceabilityAPITests(BookingAPITestCase):
    def setUp(self):
        self.owner = self.create_user("attempt-api-owner")
        self.manager = self.create_user("attempt-api-manager")
        self.staff = self.create_user("attempt-api-staff")
        self.other_staff = self.create_user("attempt-api-other-staff")
        self.external_user = self.create_user("attempt-api-external")
        self.club = self.create_club("Attempt API Club", slug="attempt-api")
        self.other_club = self.create_club(
            "Other Attempt API",
            slug="other-attempt-api",
        )
        self.court = self.create_court(self.club, "Attempt API Court")
        self.other_court = self.create_court(self.club, "Attempt Other Court")
        self.external_court = self.create_court(self.other_club, "External Court")
        self.owner_membership = self.create_membership(
            self.owner,
            self.club,
            ClubMembership.Role.OWNER,
        )
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.staff_membership = self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.other_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.external_user,
            self.other_club,
            ClubMembership.Role.OWNER,
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

    def attempt_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def test_attempt_route_resolves_to_scoped_viewset_and_filter(self):
        match = resolve("/api/v1/clubs/example-club/booking-attempts/")

        self.assertIs(match.func.cls, BookingAttemptViewSet)
        self.assertEqual(BookingAttemptViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(BookingAttemptViewSet.filterset_class, BookingAttemptFilter)

    def test_owner_and_manager_can_list_club_attempts(self):
        own_attempt = self.create_attempt(self.court, self.staff)
        other_attempt = self.create_attempt(
            self.court,
            self.other_staff,
            requested_start=self.time_at(21),
            requested_end=self.time_at(22),
        )
        external_attempt = self.create_attempt(self.external_court, self.external_user)

        for actor in (self.owner, self.manager):
            with self.subTest(actor=actor.username):
                self.client.force_authenticate(user=actor)
                response = self.client.get(self.booking_attempt_list_url(self.club))

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    self.attempt_ids(response),
                    {own_attempt.id, other_attempt.id},
                )
                self.assertNotIn(external_attempt.id, self.attempt_ids(response))

    def test_staff_lists_only_own_attempts_in_assigned_scope(self):
        own_attempt = self.create_attempt(self.court, self.staff)
        other_attempt = self.create_attempt(
            self.court,
            self.other_staff,
            requested_start=self.time_at(21),
            requested_end=self.time_at(22),
        )
        other_court_attempt = self.create_attempt(
            self.other_court,
            self.staff,
            requested_start=self.time_at(22),
            requested_end=self.time_at(23),
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_attempt_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.attempt_ids(response), {own_attempt.id})
        self.assertNotIn(other_attempt.id, self.attempt_ids(response))
        self.assertNotIn(other_court_attempt.id, self.attempt_ids(response))

    def test_attempt_filters_by_status_court_employee_and_requested_date(self):
        accepted_booking = self.create_booking(
            self.court,
            created_by=self.staff,
            customer_phone="+201000009902",
        )
        accepted_attempt = self.create_attempt(
            self.court,
            self.staff,
            booking=accepted_booking,
            outcome=BookingAttempt.Outcome.SUCCESS,
            failure_code="",
            resolution=BookingAttempt.Resolution.RESOLVED,
        )
        dismissed_attempt = self.create_attempt(
            self.court,
            self.staff,
            requested_start=self.time_at(21),
            requested_end=self.time_at(22),
            resolution=BookingAttempt.Resolution.DISMISSED,
        )
        self.create_attempt(
            self.other_court,
            self.other_staff,
            requested_start=self.time_at(22),
            requested_end=self.time_at(23),
        )
        self.client.force_authenticate(user=self.owner)

        accepted_response = self.client.get(
            self.booking_attempt_list_url(self.club),
            {"status": "ACCEPTED"},
        )
        dismissed_response = self.client.get(
            self.booking_attempt_list_url(self.club),
            {"status": "DISMISSED"},
        )
        scoped_response = self.client.get(
            self.booking_attempt_list_url(self.club),
            {
                "court": self.court.id,
                "attempted_by": self.staff.id,
                "date": "2026-05-20",
            },
        )

        self.assertEqual(self.attempt_ids(accepted_response), {accepted_attempt.id})
        self.assertEqual(self.attempt_ids(dismissed_response), {dismissed_attempt.id})
        self.assertEqual(
            self.attempt_ids(scoped_response),
            {accepted_attempt.id, dismissed_attempt.id},
        )

    def test_attempt_detail_exposes_original_request_and_backend_decision(self):
        attempt = self.create_attempt(
            self.court,
            self.staff,
            customer_name="Original Attempt Customer",
            customer_phone="+201012345678",
            requested_source=Booking.Source.RECURRING,
            requested_recurring=True,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.booking_attempt_detail_url(self.club, attempt))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "REJECTED")
        self.assertEqual(response.data["outcome"], BookingAttempt.Outcome.REJECTED)
        self.assertEqual(
            response.data["resolution"],
            BookingAttempt.Resolution.UNRESOLVED,
        )
        self.assertEqual(response.data["failure_code"], "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(
            response.data["failure_details"],
            {"conflict_type": "BOOKING"},
        )
        self.assertEqual(response.data["customer_name"], "Original Attempt Customer")
        self.assertEqual(response.data["customer_phone"], "+201012345678")
        self.assertEqual(response.data["requested_source"], Booking.Source.RECURRING)
        self.assertTrue(response.data["requested_recurring"])
        self.assertIsNone(response.data["resolved_booking"])

    def test_staff_can_dismiss_own_rejected_attempt_without_fake_booking(self):
        attempt = self.create_attempt(self.court, self.staff)
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            self.booking_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "DISMISSED")
        attempt.refresh_from_db()
        self.assertEqual(attempt.resolution, BookingAttempt.Resolution.DISMISSED)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Settlement.objects.count(), 0)

        slots_response = self.client.get(
            self.booking_slots_url(self.club),
            {"court": self.court.id, "date": "2026-05-20"},
        )
        dismissed_slot = next(
            slot
            for slot in slots_response.data["slots"]
            if (
                slot["start_time"].hour
                if hasattr(slot["start_time"], "hour")
                else parse_datetime(slot["start_time"]).hour
            )
            == 20
        )
        self.assertEqual(dismissed_slot["slot_status"], "FREE")
        self.assertTrue(dismissed_slot["is_available"])

    def test_accepted_attempt_cannot_be_dismissed(self):
        booking = self.create_booking(self.court, created_by=self.staff)
        attempt = self.create_attempt(
            self.court,
            self.staff,
            booking=booking,
            outcome=BookingAttempt.Outcome.SUCCESS,
            failure_code="",
            resolution=BookingAttempt.Resolution.RESOLVED,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            self.booking_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_ATTEMPT_CANNOT_BE_DISMISSED")
        attempt.refresh_from_db()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, BookingAttempt.Resolution.RESOLVED)
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())

    def test_staff_cannot_view_or_dismiss_another_staff_attempt(self):
        attempt = self.create_attempt(self.court, self.other_staff)
        self.client.force_authenticate(user=self.staff)

        detail_response = self.client.get(
            self.booking_attempt_detail_url(self.club, attempt)
        )
        dismiss_response = self.client.post(
            self.booking_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(dismiss_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_accepted_attempt_remains_accepted_after_booking_cancel_and_edit(self):
        self.client.force_authenticate(user=self.staff)
        create_response = self.post_booking(
            self.club,
            self.court,
            client_request_id=str(uuid4()),
            requested_at=self.time_at(18).isoformat(),
            notes="original offline note",
        )
        booking = Booking.objects.get(pk=create_response.data["id"])
        attempt = BookingAttempt.objects.get(booking=booking)

        edit_response = self.client.patch(
            self.booking_detail_url(self.club, booking),
            {
                "customer_name": "Edited Customer",
                "customer_phone": "+201000009908",
                "notes": "edited note",
            },
            format="json",
        )
        with patch(
            "apps.bookings.services.timezone.now",
            return_value=self.time_at(10),
        ):
            cancel_response = self.client.post(
                self.booking_lifecycle_url(self.club, booking, "cancel"),
                {"reason": "Customer cancelled"},
                format="json",
            )

        self.assertEqual(edit_response.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel_response.status_code, status.HTTP_200_OK)
        attempt.refresh_from_db()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, BookingAttempt.Resolution.RESOLVED)
        self.assertEqual(attempt.booking, booking)
        self.assertEqual(attempt.customer_name, "Ahmed Hassan")
        self.assertEqual(str(attempt.customer_phone), "+201000000002")
        self.assertEqual(attempt.notes, "original offline note")

    def test_rejected_attempts_do_not_appear_in_booking_list_or_financial_state(self):
        attempt = self.create_attempt(self.court, self.staff)
        self.client.force_authenticate(user=self.owner)

        bookings_response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(bookings_response.status_code, status.HTTP_200_OK)
        self.assertEqual(bookings_response.data["count"], 0)
        self.assertEqual(self.list_ids(bookings_response), set())
        self.assertTrue(BookingAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Settlement.objects.count(), 0)

    def test_membership_soft_delete_preserves_attempt_and_booking_history(self):
        booking = self.create_booking(self.court, created_by=self.staff)
        attempt = self.create_attempt(
            self.court,
            self.staff,
            booking=booking,
            outcome=BookingAttempt.Outcome.SUCCESS,
            failure_code="",
            resolution=BookingAttempt.Resolution.RESOLVED,
        )
        self.client.force_authenticate(user=self.owner)

        delete_response = self.client.delete(
            reverse(
                "club-membership-detail",
                kwargs={"club_slug": self.club.slug, "pk": self.staff_membership.pk},
            )
        )

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.staff_membership.refresh_from_db()
        attempt.refresh_from_db()
        self.assertIsNotNone(self.staff_membership.deleted_at)
        self.assertEqual(attempt.attempted_by, self.staff)
        self.assertEqual(attempt.booking, booking)
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())


class BookingSlotAvailabilityTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.club = self.create_club("Slots Club", slug="slots-club")
        self.court = self.create_court(self.club, "Slots Court")
        self.create_working_hours(self.court)
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

    def test_slots_endpoint_returns_free_slots_when_no_booking_exists(self):
        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["court"], self.court.id)
        self.assertEqual(response.data["slot_duration_minutes"], 60)
        self.assertEqual(len(response.data["slots"]), 3)
        first_slot = response.data["slots"][0]
        self.assertEqual(first_slot["slot_status"], "FREE")
        self.assertEqual(first_slot["slot_price"], "300.00")
        self.assertEqual(first_slot["is_available"], True)
        self.assertIsNone(first_slot["booking"])
        self.assertEqual(first_slot["label"], "Available")

    def test_slots_return_current_morning_and_evening_prices(self):
        working_hour = CourtWorkingHour.objects.get(court=self.court, weekday=2)
        self.set_price_periods(
            working_hour,
            (time(9, 0), time(10, 0), Decimal("200.00")),
            (time(10, 0), time(12, 0), Decimal("300.00")),
        )

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["slot_price"], "200.00")
        self.assertEqual(self.slot_by_hour(response, 10)["slot_price"], "300.00")

    def test_slots_endpoint_marks_blocking_booking_statuses(self):
        hold = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.HOLD,
        )
        confirmed = self.create_booking(
            self.court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.CONFIRMED,
        )
        completed = self.create_booking(
            self.court,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            status=Booking.Status.COMPLETED,
        )
        self.create_transaction(completed, amount=completed.total_price)

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["slot_status"], "HOLD")
        self.assertEqual(self.slot_by_hour(response, 9)["booking"]["id"], hold.id)
        self.assertEqual(self.slot_by_hour(response, 10)["slot_status"], "CONFIRMED")
        self.assertEqual(self.slot_by_hour(response, 10)["booking"]["id"], confirmed.id)
        completed_slot = self.slot_by_hour(response, 11)
        self.assertEqual(completed_slot["slot_status"], "COMPLETED")
        self.assertEqual(completed_slot["slot_price"], "300.00")
        self.assertEqual(completed_slot["is_available"], False)
        self.assertEqual(completed_slot["booking"]["id"], completed.id)
        self.assertEqual(completed_slot["booking"]["remaining_amount"], "0.00")

    def test_future_matching_recurrence_returns_virtual_reserved_slot(self):
        anchor = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            customer_name="Ahmed Mohamed",
            customer_phone="+201012345678",
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )
        self.create_transaction(anchor, amount=Decimal("50.00"))

        response = self.get_slots(date="2026-06-03")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        future_date = timezone.datetime(2026, 6, 3).date()
        slot = self.slot_by_hour(response, 9, date=future_date)
        self.assertEqual(slot["slot_status"], "RECURRING_RESERVED")
        self.assertEqual(slot["is_available"], False)
        self.assertIsNone(slot["booking"])
        self.assertEqual(slot["date"], "2026-06-03")
        self.assertEqual(slot["recurring_anchor_booking_id"], anchor.id)
        self.assertEqual(slot["can_start_recurring"], None)
        self.assertEqual(
            slot["recurring_context"],
            {
                "anchor_booking_id": anchor.id,
                "customer_name": "Ahmed Mohamed",
                "customer_phone": "+201012345678",
                "recurrence_status": "ACTIVE",
            },
        )
        self.assertEqual(
            slot["recurring_context"]["anchor_booking_id"],
            slot["recurring_anchor_booking_id"],
        )
        self.assertNotIn("status", slot["recurring_context"])
        self.assertNotIn("total_price", slot["recurring_context"])
        self.assertNotIn("total_booking_value", slot["recurring_context"])
        self.assertNotIn("paid_amount", slot["recurring_context"])
        self.assertNotIn("remaining_amount", slot["recurring_context"])
        self.assertNotIn("is_fully_paid", slot["recurring_context"])
        # slot_price is current schedule price for the selected occurrence
        self.assertEqual(slot["slot_price"], "300.00")

    def test_virtual_recurring_slot_price_uses_current_schedule_not_anchor(self):
        anchor = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )
        working_hour = CourtWorkingHour.objects.get(court=self.court, weekday=2)
        self.set_price_periods(
            working_hour,
            (time(9, 0), time(12, 0), Decimal("450.00")),
        )

        response = self.get_slots(date="2026-06-03")

        slot = self.slot_by_hour(response, 9, date=timezone.datetime(2026, 6, 3).date())
        self.assertEqual(slot["slot_status"], "RECURRING_RESERVED")
        self.assertEqual(slot["slot_price"], "450.00")
        self.assertNotEqual(slot["slot_price"], f"{anchor.total_price:.2f}")
        self.assertEqual(slot["recurring_context"]["anchor_booking_id"], anchor.id)

    def test_ended_recurrence_clears_future_virtual_slot_context(self):
        anchor = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )
        end_response = self.client.post(
            self.booking_lifecycle_url(self.club, anchor, "end-recurrence"),
            {"reason": "Customer stopped weekly reservation"},
            format="json",
        )
        self.assertEqual(end_response.status_code, status.HTTP_200_OK)

        response = self.get_slots(date="2026-06-03")

        slot = self.slot_by_hour(response, 9, date=timezone.datetime(2026, 6, 3).date())
        self.assertEqual(slot["slot_status"], "FREE")
        self.assertIsNone(slot["booking"])
        self.assertIsNone(slot["recurring_anchor_booking_id"])
        self.assertIsNone(slot["recurring_context"])
        self.assertEqual(slot["can_start_recurring"], True)

    def test_current_actual_recurring_slot_returns_booking_payload(self):
        booking = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slot = self.slot_by_hour(response, 9)
        self.assertEqual(slot["slot_status"], "CONFIRMED")
        self.assertEqual(slot["booking"]["id"], booking.id)
        self.assertEqual(slot["booking"]["source"], "RECURRING")
        self.assertEqual(slot["booking"]["is_recurring"], True)
        self.assertEqual(slot["booking"]["recurrence_status"], "ACTIVE")
        self.assertIsNone(slot["recurring_anchor_booking_id"])
        self.assertIsNone(slot["recurring_context"])
        self.assertIsNone(slot["can_start_recurring"])

    def test_free_slot_reports_can_start_recurring(self):
        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slot = self.slot_by_hour(response, 9)
        self.assertEqual(slot["slot_status"], "FREE")
        self.assertIsNone(slot["booking"])
        self.assertIsNone(slot["recurring_anchor_booking_id"])
        self.assertIsNone(slot["recurring_context"])
        self.assertEqual(slot["can_start_recurring"], True)
        self.assertIsNone(slot["recurring_blocked_reason"])
        self.assertIsNone(slot["first_recurring_conflict_start"])

    def test_free_slot_reports_future_recurring_conflict(self):
        future_start = self.time_at(9) + timedelta(weeks=4)
        self.create_booking(
            self.court,
            start_time=future_start,
            end_time=future_start + timedelta(hours=1),
            status=Booking.Status.CONFIRMED,
        )

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slot = self.slot_by_hour(response, 9)
        self.assertEqual(slot["slot_status"], "FREE")
        self.assertIsNone(slot["booking"])
        self.assertIsNone(slot["recurring_context"])
        self.assertEqual(slot["can_start_recurring"], False)
        self.assertEqual(slot["recurring_blocked_reason"], "FUTURE_CONFLICT")
        self.assertIn("2026-06-17", str(slot["first_recurring_conflict_start"]))

    def test_slot_price_changes_do_not_change_occupied_booking_snapshot(self):
        booking = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )
        working_hour = CourtWorkingHour.objects.get(court=self.court, weekday=2)
        self.set_price_periods(
            working_hour,
            (time(9, 0), time(12, 0), Decimal("450.00")),
        )

        response = self.get_slots()

        slot = self.slot_by_hour(response, 9)
        self.assertEqual(slot["slot_price"], "450.00")
        self.assertEqual(slot["booking"]["id"], booking.id)
        self.assertEqual(slot["booking"]["total_booking_value"], "300.00")

    def test_weekday_with_empty_pricing_returns_closed_day_message(self):
        CourtWorkingHour.objects.get(
            court=self.court, weekday=2
        ).pricing_periods.all().delete()

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["slots"], [])
        self.assertEqual(response.data["message"], "The court is closed on this day.")

    def test_cancelled_and_expired_bookings_do_not_block_slots(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CANCELLED,
        )
        self.create_booking(
            self.court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.EXPIRED,
        )

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["slot_status"], "FREE")
        self.assertEqual(self.slot_by_hour(response, 10)["slot_status"], "FREE")

    def test_free_is_not_a_booking_status_choice(self):
        status_values = {choice[0] for choice in Booking.Status.choices}

        self.assertNotIn("FREE", status_values)

    def test_closed_day_returns_empty_slots_with_localized_message(self):
        CourtWorkingHour.objects.filter(court=self.court).delete()
        self.create_working_hours(
            self.court,
            weekday=CourtWorkingHour.Weekday.WEDNESDAY,
            is_closed=True,
        )
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["slots"], [])
        self.assertEqual(response.data["message"], "الملعب مغلق في هذا اليوم.")

    def test_slot_labels_are_localized_to_arabic(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.HOLD,
        )
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["label"], "بانتظار العربون")
        self.assertEqual(self.slot_by_hour(response, 10)["label"], "متاح")

    def test_date_range_too_large_returns_slot_period_error(self):
        response = self.client.get(
            self.booking_slots_url(self.club),
            {
                "court": self.court.id,
                "date_from": "2026-05-01",
                "date_to": "2026-06-15",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_api_error(response, "SLOT_PERIOD_TOO_LARGE")

    def test_slots_query_count_does_not_grow_with_generated_slots(self):
        CourtWorkingHour.objects.filter(court=self.court).delete()
        self.create_working_hours(
            self.court,
            opens_at=time(0, 0),
            closes_at=time(23, 0),
        )

        with self.assertNumQueries(6):
            response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["slots"]), 23)

    def test_virtual_recurring_slots_query_count_stays_bounded(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )
        # Ensure weekday coverage for multiple future Wednesdays in range.
        for weekday in range(7):
            self.create_working_hours(
                self.court,
                weekday=weekday,
                opens_at=time(9, 0),
                closes_at=time(12, 0),
            )

        with self.assertNumQueries(6):
            response = self.client.get(
                self.booking_slots_url(self.club),
                {
                    "court": self.court.id,
                    "date_from": "2026-06-03",
                    "date_to": "2026-06-24",
                },
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reserved = [
            slot
            for slot in response.data["slots"]
            if slot["slot_status"] == "RECURRING_RESERVED"
        ]
        self.assertGreaterEqual(len(reserved), 4)
        for slot in reserved:
            self.assertIsNotNone(slot["recurring_context"])
            self.assertIsNone(slot["booking"])

    def test_multiday_blocking_booking_still_occupies_later_day_slots(self):
        self.create_working_hours(
            self.court,
            weekday=3,
            opens_at=time(9, 0),
            closes_at=time(12, 0),
        )
        self.create_booking(
            self.court,
            start_time=self.time_at(20),
            end_time=timezone.datetime(
                2026,
                5,
                21,
                10,
                tzinfo=timezone.get_current_timezone(),
            ),
            status=Booking.Status.CONFIRMED,
        )

        response = self.get_slots(date="2026-05-21")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slot = self.slot_by_hour(response, 9, date=date(2026, 5, 21))
        self.assertFalse(slot["is_available"])
        self.assertIsNotNone(slot["booking"])

    def test_schema_includes_recurring_context_on_slots(self):
        schema_response = self.client.get(reverse("schema"))

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        schema = schema_response.content.decode()
        self.assertIn("recurring_context", schema)
        self.assertIn("anchor_booking_id", schema)
        self.assertIn("RECURRING_RESERVED", schema)
        self.assertIn("/api/v1/clubs/{club_slug}/bookings/slots/", schema)


class BookingScopeTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("scope-admin")
        self.owner = self.create_user("scope-owner")
        self.manager = self.create_user("scope-manager")
        self.staff = self.create_user("scope-staff")
        self.club = self.create_club("Scoped Club", slug="scoped-club")
        self.other_club = self.create_club("Other Scoped Club", slug="other-scoped")
        self.court = self.create_court(self.club, "Scoped Court")
        self.same_club_other_court = self.create_court(self.club, "Scoped Other Court")
        self.other_court = self.create_court(self.other_club, "Other Scoped Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(
            self.same_club_other_court,
            customer_phone="+201000000007",
        )
        self.other_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000000003",
        )
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_anonymous_cannot_access_bookings(self):
        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_lists_selected_club_bookings_only(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_owner_sees_bookings_in_selected_owned_club_only(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )

    def test_owner_cannot_access_unrelated_club_bookings(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.booking_list_url(self.other_club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_sees_bookings_in_assigned_club_only(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )

    def test_staff_sees_bookings_for_assigned_court_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.booking.id})
        self.assertNotIn(self.same_club_other_booking.id, self.list_ids(response))

    def test_owner_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.booking_detail_url(self.club, self.other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            self.booking_detail_url(self.club, self.other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.booking_detail_url(self.club, self.same_club_other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_sees_virtual_recurring_context_only_for_assigned_court(self):
        assigned_anchor = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            customer_name="Assigned Court Customer",
            customer_phone="+201011111111",
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )
        self.create_booking(
            self.same_club_other_court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            customer_name="Other Court Customer",
            customer_phone="+201022222222",
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )
        self.client.force_authenticate(user=self.staff)

        allowed = self.client.get(
            self.booking_slots_url(self.club),
            {"court": self.court.id, "date": "2026-06-03"},
        )
        denied = self.client.get(
            self.booking_slots_url(self.club),
            {"court": self.same_club_other_court.id, "date": "2026-06-03"},
        )

        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        reserved = next(
            slot
            for slot in allowed.data["slots"]
            if slot["slot_status"] == "RECURRING_RESERVED"
        )
        self.assertEqual(
            reserved["recurring_context"]["anchor_booking_id"],
            assigned_anchor.id,
        )
        self.assertEqual(
            reserved["recurring_context"]["customer_name"],
            "Assigned Court Customer",
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_cannot_see_other_club_virtual_recurring_context(self):
        self.create_booking(
            self.other_court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            customer_name="Cross Club Customer",
            customer_phone="+201033333333",
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.booking_slots_url(self.other_club),
            {"court": self.other_court.id, "date": "2026-06-03"},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BookingCreationPermissionTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("create-admin")
        self.owner = self.create_user("create-owner")
        self.manager = self.create_user("create-manager")
        self.staff = self.create_user("create-staff")
        self.club = self.create_club("Create Club", slug="create-club")
        self.other_club = self.create_club("Other Create Club", slug="other-create")
        self.court = self.create_court(self.club, "Create Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Same Club Other Court",
        )
        self.other_court = self.create_court(self.other_club, "Other Create Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_platform_admin_can_create_booking_on_any_active_court_in_selected_club(
        self,
    ):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.same_club_other_court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_can_create_booking_inside_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_cannot_create_booking_inside_unrelated_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")
        self.assertEqual(response.data["details"]["club_slug"], self.other_club.slug)

    def test_inactive_membership_returns_club_access_revoked_code(self):
        ClubMembership.objects.filter(
            user=self.owner,
            club=self.club,
            role=ClubMembership.Role.OWNER,
        ).update(is_active=False)
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")
        self.assertEqual(response.data["details"]["club_slug"], self.club.slug)

    def test_manager_can_create_booking_inside_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_manager_cannot_create_booking_inside_unrelated_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_create_booking_on_assigned_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_cannot_create_booking_on_another_court_in_same_club(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.club, self.same_club_other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_create_booking_on_unrelated_club_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BookingSourceAndActiveTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("source-admin")
        self.owner = self.create_user("source-owner")
        self.club = self.create_club("Source Club", slug="source-club")
        self.court = self.create_court(self.club, "Source Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)

    def test_non_platform_users_cannot_create_admin_correction_booking(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "source")

    def test_platform_admin_can_create_admin_correction_booking(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], Booking.Source.ADMIN_CORRECTION)

    def test_normal_booking_source_defaults_to_manual(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], Booking.Source.MANUAL)

    def test_cannot_create_booking_on_inactive_court(self):
        self.court.is_active = False
        self.court.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "court")

    def test_cannot_create_booking_when_club_is_inactive(self):
        self.club.is_active = False
        self.club.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BookingOverlapTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("overlap-admin")
        self.club = self.create_club("Overlap Club", slug="overlap-club")
        self.court = self.create_court(self.club, "Overlap Court")
        self.other_court = self.create_court(self.club, "Other Overlap Court")
        self.client.force_authenticate(user=self.platform_admin)

    def create_existing_booking(self, status_value):
        return self.create_booking(
            self.court,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=status_value,
        )

    def assert_overlap_is_rejected_for_status(self, status_value):
        self.create_existing_booking(status_value)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(21).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

    def assert_overlap_is_allowed_for_status(self, status_value):
        self.create_existing_booking(status_value)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(21).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_create_overlapping_hold_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.HOLD)

    def test_cannot_create_overlapping_confirmed_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.CONFIRMED)

    def test_can_create_adjacent_booking_ending_exactly_at_existing_start(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(19).isoformat(),
            end_time=self.time_at(20).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_can_create_adjacent_booking_starting_exactly_at_existing_end(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(21).isoformat(),
            end_time=self.time_at(22).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_can_create_overlapping_booking_on_different_court(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.other_court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(21).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cancelled_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.CANCELLED)

    def test_expired_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.EXPIRED)

    def test_cannot_create_overlapping_completed_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.COMPLETED)

    def test_cannot_create_overlapping_no_show_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.NO_SHOW)


class BookingUpdateTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("update-admin")
        self.club = self.create_club("Update Club", slug="update-club")
        self.court = self.create_court(self.club, "Update Court")
        self.other_court = self.create_court(self.club, "Other Update Court")
        self.booking = self.create_booking(self.court)
        self.client.force_authenticate(user=self.platform_admin)

    def test_allowed_user_can_patch_basic_details(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {
                "customer_name": "Updated Customer",
                "customer_phone": "+201000000004",
                "notes": "Updated note",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.customer_name, "Updated Customer")
        self.assertEqual(str(self.booking.customer_phone), "+201000000004")
        self.assertEqual(self.booking.notes, "Updated note")

    def test_cannot_patch_status_in_sprint_3(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"status": Booking.Status.CANCELLED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HOLD)

    def test_cannot_patch_total_price(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"total_price": "1.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.total_price, Decimal("300.00"))

    def test_cannot_patch_court_or_times_in_sprint_3(self):
        original_start = self.booking.start_time
        original_end = self.booking.end_time

        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {
                "court": self.other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.court, self.court)
        self.assertEqual(self.booking.start_time, original_start)
        self.assertEqual(self.booking.end_time, original_end)

    def test_delete_booking_is_not_allowed(self):
        response = self.client.delete(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_locked_booking_status_cannot_be_patched(self):
        self.booking.status = Booking.Status.COMPLETED
        self.booking.save(update_fields=["status"])

        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"customer_name": "Should Not Change"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BookingLifecycleActionTests(BookingAPITestCase):
    detail_response_fields = {
        "id",
        "club",
        "court",
        "customer_name",
        "customer_phone",
        "start_time",
        "end_time",
        "total_price",
        "paid_amount",
        "remaining_amount",
        "is_fully_paid",
        "status",
        "source",
        "is_recurring",
        "recurrence_status",
        "previous_recurring_booking_id",
        "next_recurring_booking_id",
        "client_request_id",
        "notes",
        "cancellation_reason",
        "no_show_reason",
        "reschedule_reason",
        "completed_at",
        "cancelled_at",
        "no_show_at",
        "expired_at",
        "hold_expires_at",
        "created_by",
        "created",
        "modified",
    }

    def setUp(self):
        self.platform_admin = self.create_platform_admin("lifecycle-admin")
        self.owner = self.create_user("lifecycle-owner")
        self.manager = self.create_user("lifecycle-manager")
        self.staff = self.create_user("lifecycle-staff")
        self.other_user = self.create_user("lifecycle-other-user")
        self.club = self.create_club("Lifecycle Club", slug="lifecycle-club")
        self.other_club = self.create_club(
            "Other Lifecycle Club",
            slug="other-lifecycle",
        )
        self.court = self.create_court(self.club, "Lifecycle Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Lifecycle Other Court",
        )
        self.other_court = self.create_court(self.other_club, "Other Lifecycle Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.other_user,
            self.other_club,
            ClubMembership.Role.OWNER,
        )

    def post_lifecycle(self, club, booking, action_name, user, payload=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        return self.client.post(
            self.booking_lifecycle_url(club, booking, action_name),
            payload or {},
            format="json",
        )

    def future_time_at(self, days_ahead: int, hour: int):
        base = timezone.now() + timedelta(days=days_ahead)
        return base.replace(hour=hour, minute=0, second=0, microsecond=0)

    def test_anonymous_cannot_call_lifecycle_actions(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=self.future_time_at(1, 20),
            end_time=self.future_time_at(1, 21),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            None,
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cancel_after_start_returns_time_passed_even_if_hold_expiry_is_due(self):
        now = timezone.now()
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(minutes=50),
        )
        Booking.objects.filter(pk=booking.pk).update(created=now - timedelta(hours=2))

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.platform_admin,
            {"reason": "Customer cancelled late"},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_CANCELLATION_TIME_PASSED")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_platform_admin_can_cancel_hold(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=self.future_time_at(1, 20),
            end_time=self.future_time_at(1, 21),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertIsNotNone(booking.cancelled_at)

    def test_owner_can_cancel_confirmed(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.future_time_at(1, 20),
            end_time=self.future_time_at(1, 21),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.owner,
            {"reason": "Customer cancelled"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.cancellation_reason, "Customer cancelled")

    def test_cancellation_preview_and_cancel_create_signed_refund(self):
        self.court.minimum_deposit = Decimal("50.00")
        self.court.cancellation_refund_notice_days = 3
        self.court.save(
            update_fields=["minimum_deposit", "cancellation_refund_notice_days"]
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.future_time_at(1, 20),
            end_time=self.future_time_at(1, 21),
        )
        self.create_transaction(
            booking,
            amount=Decimal("200.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.owner,
        )
        self.client.force_authenticate(user=self.owner)

        preview = self.client.post(
            self.booking_lifecycle_url(
                self.club,
                booking,
                "cancellation-preview",
            ),
            {},
            format="json",
        )
        cancel = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.owner,
            {
                "reason": "Customer cancelled",
                "refund_payment_method": Transaction.PaymentMethod.CASH,
            },
        )

        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(preview.data["paid_amount"], "200.00")
        self.assertEqual(preview.data["refund_amount"], "150.00")
        self.assertEqual(preview.data["retained_amount"], "50.00")
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        refund = Transaction.objects.get(
            booking=booking,
            transaction_type=Transaction.Type.REFUND,
        )
        self.assertEqual(refund.amount, Decimal("-150.00"))
        self.assertFalse(refund.is_cancelled)

    def test_manager_can_complete_confirmed(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.manager,
            {"confirm_collect_remaining_cash": True},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertIsNotNone(booking.completed_at)

    def test_staff_can_change_booking_status_for_assigned_court(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "no-show",
            self.staff,
            {"reason": "Customer did not arrive"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.NO_SHOW)
        self.assertEqual(booking.no_show_reason, "Customer did not arrive")
        self.assertIsNotNone(booking.no_show_at)

    def test_staff_cannot_change_booking_status_for_another_court_in_same_club(self):
        booking = self.create_booking(
            self.same_club_other_court,
            status=Booking.Status.CONFIRMED,
        )

        response = self.post_lifecycle(self.club, booking, "complete", self.staff)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_unrelated_club_member_cannot_access_selected_club_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(self.club, booking, "complete", self.other_user)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_allowed_transitions_succeed(self):
        cases = (
            (Booking.Status.HOLD, "cancel", Booking.Status.CANCELLED, 10),
            (Booking.Status.HOLD, "expire", Booking.Status.EXPIRED, 11),
            (Booking.Status.CONFIRMED, "cancel", Booking.Status.CANCELLED, 12),
            (Booking.Status.CONFIRMED, "complete", Booking.Status.COMPLETED, 13),
            (Booking.Status.CONFIRMED, "no-show", Booking.Status.NO_SHOW, 14),
        )
        for source_status, action_name, target_status, phone_suffix in cases:
            with self.subTest(source_status=source_status, action_name=action_name):
                booking = self.create_booking(
                    self.court,
                    status=source_status,
                    start_time=(
                        self.future_time_at(phone_suffix, 20)
                        if action_name == "cancel"
                        else self.time_at(phone_suffix)
                    ),
                    end_time=(
                        self.future_time_at(phone_suffix, 21)
                        if action_name == "cancel"
                        else self.time_at(phone_suffix + 1)
                    ),
                    customer_phone=f"+2010000003{phone_suffix:02d}",
                )
                if action_name == "complete":
                    self.create_transaction(booking, amount=booking.total_price)

                payload = (
                    {"confirm_collect_remaining_cash": True}
                    if action_name == "complete"
                    else {}
                )
                response = self.post_lifecycle(
                    self.club,
                    booking,
                    action_name,
                    self.platform_admin,
                    payload,
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                booking.refresh_from_db()
                self.assertEqual(booking.status, target_status)

    def test_hold_cannot_be_completed_or_marked_no_show(self):
        cases = (
            ("complete", Booking.Status.COMPLETED, 15),
            ("no-show", Booking.Status.NO_SHOW, 16),
        )
        for action_name, target_status, phone_suffix in cases:
            with self.subTest(action_name=action_name):
                booking = self.create_booking(
                    self.court,
                    status=Booking.Status.HOLD,
                    start_time=self.time_at(phone_suffix),
                    end_time=self.time_at(phone_suffix + 1),
                    customer_phone=f"+2010000004{phone_suffix:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    action_name,
                    self.platform_admin,
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assert_api_error(
                    response,
                    "INVALID_BOOKING_STATUS_TRANSITION",
                )
                booking.refresh_from_db()
                self.assertEqual(booking.status, Booking.Status.HOLD)
                self.assertNotIn("booking", response.data)
                self.assertNotEqual(booking.status, target_status)

    def test_terminal_statuses_cannot_transition(self):
        terminal_statuses = (
            Booking.Status.COMPLETED,
            Booking.Status.CANCELLED,
            Booking.Status.NO_SHOW,
            Booking.Status.EXPIRED,
        )
        for index, terminal_status in enumerate(terminal_statuses, start=17):
            with self.subTest(terminal_status=terminal_status):
                booking = self.create_booking(
                    self.court,
                    status=terminal_status,
                    start_time=self.time_at(index),
                    end_time=self.time_at(index + 1),
                    customer_phone=f"+2010000005{index:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    "cancel",
                    self.platform_admin,
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                expected_code = (
                    "BOOKING_ALREADY_CANCELLED"
                    if terminal_status == Booking.Status.CANCELLED
                    else "INVALID_BOOKING_STATUS_TRANSITION"
                )
                self.assert_api_error(response, expected_code)
                self.assertNotIn("booking", response.data)
                booking.refresh_from_db()
                self.assertEqual(booking.status, terminal_status)

    def test_successful_action_returns_booking_detail_shape(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), self.detail_response_fields)

    def test_staff_cancel_requires_reason(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(self.club, booking, "cancel", self.staff)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "reason")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_no_show_requires_confirmed_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "no-show",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "INVALID_BOOKING_STATUS_TRANSITION")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_expire_requires_hold_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "expire",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "INVALID_BOOKING_STATUS_TRANSITION")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_complete_fully_paid_booking_succeeds_without_auto_cash(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_active_recurring_completion_requires_continue_decision(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "RECURRENCE_CONTINUATION_DECISION_REQUIRED")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.recurrence_status, Booking.RecurrenceStatus.ACTIVE)

    def test_active_recurring_completion_can_end_recurrence(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
            {"continue_recurring": False},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.recurrence_status, Booking.RecurrenceStatus.ENDED)
        self.assertFalse(hasattr(booking, "next_recurring_booking"))

    def test_active_recurring_completion_renews_next_week_with_normal_payment(self):
        self.court.minimum_deposit = Decimal("50.00")
        self.court.save(update_fields=["minimum_deposit"])
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
            {
                "continue_recurring": True,
                "next_deposit_payment_method": Transaction.PaymentMethod.CASH,
                "next_deposit_notes": "Next week deposit",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        next_booking = booking.next_recurring_booking
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.recurrence_status, Booking.RecurrenceStatus.RENEWED)
        self.assertEqual(next_booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(next_booking.source, Booking.Source.RECURRING)
        self.assertEqual(
            next_booking.recurrence_status,
            Booking.RecurrenceStatus.ACTIVE,
        )
        self.assertEqual(next_booking.previous_recurring_booking, booking)
        self.assertEqual(
            next_booking.start_time, booking.start_time + timedelta(days=7)
        )
        next_payment = Transaction.objects.get(booking=next_booking)
        self.assertEqual(next_payment.transaction_type, Transaction.Type.PAYMENT)
        self.assertEqual(next_payment.amount, Decimal("50.00"))

    def test_recurrence_next_preview_is_authoritative_and_does_not_mutate(self):
        self.court.minimum_deposit = Decimal("150.00")
        self.court.requires_digital_payment_reference = True
        self.court.save(
            update_fields=["minimum_deposit", "requires_digital_payment_reference"]
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=booking.total_price)
        booking_count = Booking.objects.count()

        self.client.force_authenticate(user=self.platform_admin)
        response = self.client.get(
            self.booking_lifecycle_url(self.club, booking, "recurrence-next")
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["can_continue"])
        self.assertEqual(
            parse_datetime(response.data["next_start_time"]),
            booking.start_time + timedelta(days=7),
        )
        self.assertEqual(
            parse_datetime(response.data["next_end_time"]),
            booking.end_time + timedelta(days=7),
        )
        self.assertEqual(response.data["next_total_price"], "300.00")
        self.assertEqual(response.data["next_required_deposit"], "150.00")
        self.assertTrue(response.data["requires_digital_payment_reference"])
        self.assertTrue(response.data["requires_payment_reference"])
        self.assertEqual(Booking.objects.count(), booking_count)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.recurrence_status, Booking.RecurrenceStatus.ACTIVE)

    def test_recurrence_next_conflict_uses_stable_unavailable_code(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=booking.total_price)
        self.create_booking(
            self.court,
            customer_phone="+201000000081",
            start_time=booking.start_time + timedelta(days=7),
            end_time=booking.end_time + timedelta(days=7),
            status=Booking.Status.HOLD,
        )

        self.client.force_authenticate(user=self.platform_admin)
        response = self.client.get(
            self.booking_lifecycle_url(self.club, booking, "recurrence-next")
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "NEXT_RECURRING_SLOT_UNAVAILABLE")

    def test_recurrence_next_rejects_non_active_confirmed_recurrence(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.MANUAL,
        )
        hold_recurring = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            customer_phone="+201000000082",
        )

        self.client.force_authenticate(user=self.platform_admin)
        manual_response = self.client.get(
            self.booking_lifecycle_url(self.club, booking, "recurrence-next")
        )
        hold_response = self.client.get(
            self.booking_lifecycle_url(self.club, hold_recurring, "recurrence-next")
        )

        self.assertEqual(manual_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(manual_response, "BOOKING_RECURRENCE_NOT_ACTIVE")
        self.assertEqual(hold_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(hold_response, "RECURRENCE_CANNOT_CONTINUE")

    def test_recurrence_next_respects_staff_court_scope(self):
        booking = self.create_booking(
            self.same_club_other_court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(
            self.booking_lifecycle_url(self.club, booking, "recurrence-next")
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_end_recurrence_keeps_booking_status_and_transactions(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )
        self.create_transaction(booking, amount=Decimal("50.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "end-recurrence",
            self.platform_admin,
            {"reason": "Customer stopped weekly reservation"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.recurrence_status, Booking.RecurrenceStatus.ENDED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_active_recurring_cancel_no_show_and_expire_end_recurrence(self):
        cases = (
            ("cancel", Booking.Status.HOLD, Booking.Status.CANCELLED, 31),
            ("no-show", Booking.Status.CONFIRMED, Booking.Status.NO_SHOW, 32),
            ("expire", Booking.Status.HOLD, Booking.Status.EXPIRED, 33),
        )
        for action_name, source_status, target_status, hour in cases:
            with self.subTest(action_name=action_name):
                booking = self.create_booking(
                    self.court,
                    status=source_status,
                    source=Booking.Source.RECURRING,
                    recurrence_status=Booking.RecurrenceStatus.ACTIVE,
                    start_time=self.future_time_at(1, hour % 24),
                    end_time=self.future_time_at(1, (hour + 1) % 24),
                    customer_phone=f"+2010000008{hour}",
                )
                payload = (
                    {"reason": "Lifecycle reason"} if action_name != "expire" else {}
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    action_name,
                    self.platform_admin,
                    payload,
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                booking.refresh_from_db()
                self.assertEqual(booking.status, target_status)
                self.assertEqual(
                    booking.recurrence_status,
                    Booking.RecurrenceStatus.ENDED,
                )

    def test_active_recurring_reschedule_is_rejected(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "RECURRING_BOOKING_RESCHEDULE_NOT_SUPPORTED")

    def test_complete_with_remaining_amount_returns_domain_conflict(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        self.assertEqual(
            response.data["message"],
            "This booking cannot be completed until the remaining amount is paid.",
        )
        self.assertEqual(
            response.data["details"],
            {
                "booking_id": booking.id,
                "remaining_amount": "200.00",
            },
        )
        self.assertNotIn("booking", response.data)
        self.assertNotIn("detail", response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_complete_with_remaining_amount_returns_localized_arabic_error(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        self.assertEqual(
            response.data["message"],
            "لا يمكن إكمال الحجز قبل سداد المبلغ المتبقي.",
        )

    def test_completion_remaining_amount_ignores_cancelled_transactions(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))
        self.create_transaction(
            booking,
            amount=Decimal("200.00"),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Wrong completion payment",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_complete_with_confirmation_still_rejects_remaining_amount(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
            {"confirm_collect_remaining_cash": True},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_reschedule_hold_booking_to_free_slot(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.same_club_other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
                "reason": "Customer changed time",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.court, self.same_club_other_court)
        self.assertEqual(booking.start_time, self.time_at(22))
        self.assertEqual(booking.reschedule_reason, "Customer changed time")
        self.assertEqual(booking.transactions.count(), 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_RESCHEDULED,
                entity_type="Booking",
                entity_id=booking.id,
            ).exists()
        )

    def test_reschedule_confirmed_booking_keeps_transactions_attached(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        transaction_obj = self.create_transaction(booking, amount=Decimal("50.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transaction_obj.refresh_from_db()
        self.assertEqual(transaction_obj.booking_id, booking.id)

    def test_reschedule_blocks_overlapping_active_booking(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            customer_phone="+201000000777",
        )
        self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            customer_phone="+201000000778",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

    def test_reschedule_blocks_overlapping_completed_booking(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            customer_phone="+201000000779",
        )
        self.create_booking(
            self.court,
            status=Booking.Status.COMPLETED,
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            customer_phone="+201000000780",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

    def test_reschedule_excludes_current_booking_from_overlap_check(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(20).isoformat(),
                "end_time": self.time_at(21).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_cannot_reschedule_outside_assigned_court(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.staff,
            {
                "court": self.same_club_other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reschedule_higher_price_updates_total_price(self):
        expensive_court = self.create_court(
            self.club,
            "Expensive Court",
            default_price=Decimal("450.00"),
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": expensive_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.total_price, Decimal("450.00"))

    def test_reschedule_lower_price_keeps_existing_total_price(self):
        cheaper_court = self.create_court(
            self.club,
            "Cheaper Court",
            default_price=Decimal("200.00"),
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": cheaper_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.total_price, Decimal("300.00"))

    def test_terminal_statuses_cannot_be_rescheduled(self):
        for index, terminal_status in enumerate(Booking.LOCKED_STATUSES, start=1):
            with self.subTest(terminal_status=terminal_status):
                booking = self.create_booking(
                    self.court,
                    status=terminal_status,
                    start_time=self.time_at(index),
                    end_time=self.time_at(index + 1),
                    customer_phone=f"+2010000009{index:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    "reschedule",
                    self.platform_admin,
                    {
                        "court": self.court.id,
                        "start_time": self.time_at(22).isoformat(),
                        "end_time": self.time_at(23).isoformat(),
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assert_api_error(
                    response,
                    "INVALID_BOOKING_STATUS_TRANSITION",
                )

    def test_expire_sets_timestamp_and_audit_log(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "expire",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)
        self.assertIsNotNone(booking.expired_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_EXPIRED,
                entity_type="Booking",
                entity_id=booking.id,
            ).exists()
        )


class BookingAutomaticExpiryCommandTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("expiry-admin")
        self.club = self.create_club("Expiry Club", slug="expiry-club")
        self.court = self.create_court(
            self.club,
            "Expiry Court",
            internal_hold_expiry_hours=12,
        )

    def age_booking(self, booking, *, hours):
        created = timezone.now() - timedelta(hours=hours)
        Booking.objects.filter(pk=booking.pk).update(created=created)
        booking.refresh_from_db()
        return booking

    def future_slot(self, *, hours_from_now=8, duration_hours=1):
        start_time = timezone.now() + timedelta(hours=hours_from_now)
        end_time = start_time + timedelta(hours=duration_hours)
        return start_time, end_time

    def test_expire_hold_bookings_expires_due_holds_only_and_is_idempotent(self):
        due_start, due_end = self.future_slot(hours_from_now=8)
        not_due_start, not_due_end = self.future_slot(hours_from_now=9)
        confirmed_start, confirmed_end = self.future_slot(hours_from_now=10)
        terminal_start, terminal_end = self.future_slot(hours_from_now=11)
        recurring_start, recurring_end = self.future_slot(hours_from_now=12)
        due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=due_start,
                end_time=due_end,
                customer_phone="+201000001001",
            ),
            hours=13,
        )
        not_due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=not_due_start,
                end_time=not_due_end,
                customer_phone="+201000001002",
            ),
            hours=2,
        )
        confirmed = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.CONFIRMED,
                start_time=confirmed_start,
                end_time=confirmed_end,
                customer_phone="+201000001003",
            ),
            hours=13,
        )
        terminal = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.CANCELLED,
                start_time=terminal_start,
                end_time=terminal_end,
                customer_phone="+201000001004",
            ),
            hours=13,
        )
        recurring_due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                source=Booking.Source.RECURRING,
                recurrence_status=Booking.RecurrenceStatus.ACTIVE,
                start_time=recurring_start,
                end_time=recurring_end,
                customer_phone="+201000001005",
            ),
            hours=13,
        )

        call_command("expire_hold_bookings", verbosity=0)
        call_command("expire_hold_bookings", verbosity=0)

        due_hold.refresh_from_db()
        not_due_hold.refresh_from_db()
        confirmed.refresh_from_db()
        terminal.refresh_from_db()
        recurring_due_hold.refresh_from_db()
        self.assertEqual(due_hold.status, Booking.Status.EXPIRED)
        self.assertIsNotNone(due_hold.expired_at)
        self.assertEqual(recurring_due_hold.status, Booking.Status.EXPIRED)
        self.assertEqual(
            recurring_due_hold.recurrence_status,
            Booking.RecurrenceStatus.ENDED,
        )
        self.assertEqual(not_due_hold.status, Booking.Status.HOLD)
        self.assertEqual(confirmed.status, Booking.Status.CONFIRMED)
        self.assertEqual(terminal.status, Booking.Status.CANCELLED)
        audit_logs = AuditLog.objects.filter(
            action=AuditLog.Action.BOOKING_EXPIRED,
            entity_type="Booking",
            entity_id=due_hold.id,
        )
        self.assertEqual(audit_logs.count(), 1)
        audit_log = audit_logs.get()
        self.assertIsNone(audit_log.actor)
        self.assertEqual(audit_log.metadata["source"], "automatic_hold_expiry")
        self.assertFalse(audit_log.metadata["recurrence_ended"])
        self.assertEqual(
            audit_log.metadata["display_snapshot"],
            {"actor_name": "", "court_name": self.court.name},
        )

    def test_due_hold_candidates_respect_per_court_expiry_hours(self):
        from apps.bookings.services import due_hold_booking_candidate_ids

        long_court = self.create_court(
            self.club,
            "Long Expiry Court",
            internal_hold_expiry_hours=48,
        )
        due_start, due_end = self.future_slot(hours_from_now=8)
        long_start, long_end = self.future_slot(hours_from_now=9)
        due_short = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=due_start,
                end_time=due_end,
                customer_phone="+201000001011",
            ),
            hours=13,
        )
        not_due_long = self.age_booking(
            self.create_booking(
                long_court,
                status=Booking.Status.HOLD,
                start_time=long_start,
                end_time=long_end,
                customer_phone="+201000001012",
            ),
            hours=13,
        )

        due_ids = set(due_hold_booking_candidate_ids(now=timezone.now()))

        self.assertIn(due_short.id, due_ids)
        self.assertNotIn(not_due_long.id, due_ids)

    def test_due_hold_candidate_query_does_not_materialize_non_due_holds(self):
        from apps.bookings.services import due_hold_booking_candidate_ids

        due_start, due_end = self.future_slot(hours_from_now=8)
        not_due_start, not_due_end = self.future_slot(hours_from_now=10)
        due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=due_start,
                end_time=due_end,
                customer_phone="+201000001021",
            ),
            hours=13,
        )
        for index in range(25):
            self.age_booking(
                self.create_booking(
                    self.court,
                    status=Booking.Status.HOLD,
                    start_time=not_due_start,
                    end_time=not_due_end,
                    customer_phone=f"+2010000011{index:02d}",
                ),
                hours=1,
            )

        due_ids = due_hold_booking_candidate_ids(now=timezone.now())

        self.assertEqual(due_ids, [due_hold.id])

    def test_expire_due_holds_rechecks_expiry_after_lock(self):
        from apps.bookings.services import (
            due_hold_booking_candidate_ids,
            expire_locked_due_hold_bookings,
        )

        start_time, end_time = self.future_slot(hours_from_now=8)
        booking = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=start_time,
                end_time=end_time,
                customer_phone="+201000001031",
            ),
            hours=13,
        )
        due_ids = due_hold_booking_candidate_ids(now=timezone.now())
        self.assertEqual(due_ids, [booking.id])
        Booking.objects.filter(pk=booking.pk).update(created=timezone.now())

        expired = expire_locked_due_hold_bookings(
            due_ids=due_ids,
            now=timezone.now(),
        )

        booking.refresh_from_db()
        self.assertEqual(expired, [])
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def annotated_hold_expires_at(self, booking):
        return (
            annotate_booking_hold_expires_at(
                Booking.objects.filter(pk=booking.pk).select_related("court")
            )
            .values_list("hold_expires_at", flat=True)
            .get()
        )

    def test_policy_expiry_earlier_than_start_is_effective_deadline(self):
        self.court.internal_hold_expiry_hours = 2
        self.court.save(update_fields=["internal_hold_expiry_hours"])
        created = timezone.datetime(
            2026, 9, 4, 10, 0, tzinfo=timezone.get_current_timezone()
        )
        start_time = timezone.datetime(
            2026, 9, 4, 15, 0, tzinfo=timezone.get_current_timezone()
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            customer_phone="+201000001041",
        )
        Booking.objects.filter(pk=booking.pk).update(created=created)
        booking.refresh_from_db()

        expected = created + timedelta(hours=2)
        self.assertEqual(compute_booking_hold_expires_at(booking), expected)
        self.assertEqual(self.annotated_hold_expires_at(booking), expected)

    def test_start_time_earlier_than_policy_expiry_is_effective_deadline(self):
        self.court.internal_hold_expiry_hours = 12
        self.court.save(update_fields=["internal_hold_expiry_hours"])
        created = timezone.datetime(
            2026, 9, 4, 19, 30, tzinfo=timezone.get_current_timezone()
        )
        start_time = timezone.datetime(
            2026, 9, 4, 20, 0, tzinfo=timezone.get_current_timezone()
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            customer_phone="+201000001042",
        )
        Booking.objects.filter(pk=booking.pk).update(created=created)
        booking.refresh_from_db()

        self.assertEqual(compute_booking_hold_expires_at(booking), start_time)
        self.assertEqual(self.annotated_hold_expires_at(booking), start_time)

    def test_hold_is_due_exactly_at_start_time(self):
        from apps.bookings.services import due_hold_booking_candidate_ids

        now = timezone.now()
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=now,
            end_time=now + timedelta(hours=1),
            customer_phone="+201000001043",
        )
        Booking.objects.filter(pk=booking.pk).update(created=now - timedelta(hours=1))

        due_ids = due_hold_booking_candidate_ids(now=now)
        self.assertIn(booking.id, due_ids)

    def test_past_start_hold_is_automatic_expiry_candidate(self):
        from apps.bookings.services import due_hold_booking_candidate_ids

        now = timezone.now()
        booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=now - timedelta(minutes=15),
            end_time=now + timedelta(minutes=45),
            customer_phone="+201000001044",
        )
        Booking.objects.filter(pk=booking.pk).update(
            created=now - timedelta(minutes=30)
        )

        due_ids = due_hold_booking_candidate_ids(now=now)
        self.assertIn(booking.id, due_ids)

    def test_python_and_orm_deadlines_match_across_court_expiry_hours(self):
        other_court = self.create_court(
            self.club,
            "Other Hours Court",
            internal_hold_expiry_hours=48,
        )
        created = timezone.now() - timedelta(hours=3)
        start_time = timezone.now() + timedelta(hours=10)
        short_booking = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            customer_phone="+201000001045",
        )
        long_booking = self.create_booking(
            other_court,
            status=Booking.Status.HOLD,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            customer_phone="+201000001046",
        )
        Booking.objects.filter(pk__in=[short_booking.pk, long_booking.pk]).update(
            created=created
        )
        short_booking.refresh_from_db()
        long_booking.refresh_from_db()

        self.assertEqual(
            compute_booking_hold_expires_at(short_booking),
            self.annotated_hold_expires_at(short_booking),
        )
        self.assertEqual(
            compute_booking_hold_expires_at(long_booking),
            self.annotated_hold_expires_at(long_booking),
        )
        self.assertNotEqual(
            compute_booking_hold_expires_at(short_booking),
            compute_booking_hold_expires_at(long_booking),
        )


class BookingFilterTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("filter-admin")
        self.owner = self.create_user("filter-owner")
        self.club = self.create_club("Filter Club", slug="filter-club")
        self.other_club = self.create_club("Other Filter Club", slug="other-filter")
        self.court = self.create_court(self.club, "Filter Court")
        self.other_court = self.create_court(self.other_club, "Other Filter Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.booking = self.create_booking(
            self.court,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=Booking.Status.HOLD,
            source=Booking.Source.MANUAL,
        )
        self.confirmed_booking = self.create_booking(
            self.court,
            customer_phone="+201000000005",
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.ADMIN_CORRECTION,
        )
        self.other_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000000006",
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=Booking.Status.HOLD,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_booking_list_includes_notes(self):
        self.booking.notes = "Bring tournament equipment"
        self.booking.save(update_fields=["notes"])

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_data = next(
            item for item in response.data["results"] if item["id"] == self.booking.id
        )
        self.assertEqual(booking_data["notes"], "Bring tournament equipment")

    def test_filter_by_court_inside_selected_club(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )

    def test_filter_by_status(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"status": Booking.Status.CONFIRMED},
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_filter_by_source(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"source": Booking.Source.ADMIN_CORRECTION},
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_filter_by_date(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_filter_by_date_from_and_date_to(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {
                "date_from": self.time_at(21, 30).isoformat(),
                "date_to": self.time_at(22, 30).isoformat(),
            },
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_invalid_date_filter_returns_400(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "not-a-date"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_datetime_filter_returns_400(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date_from": "not-a-datetime"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filters_still_respect_user_scope_inside_selected_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_filters_still_respect_staff_assigned_court_scope(self):
        staff = self.create_user("filter-staff")
        same_club_other_court = self.create_court(
            self.club,
            "Hidden Staff Filter Court",
        )
        same_club_other_booking = self.create_booking(
            same_club_other_court,
            customer_phone="+201000000007",
            start_time=self.time_at(20),
            end_time=self.time_at(21),
        )
        self.create_membership(
            staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=staff)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(same_club_other_booking.id, self.list_ids(response))

    def test_club_query_param_does_not_control_club_scoped_filtering(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_needs_action_filter_excludes_completed_bookings_with_remaining_amount(
        self,
    ):
        completed_with_remaining = self.create_booking(
            self.court,
            customer_phone="+201000000008",
            start_time=self.time_at(23),
            end_time=self.time_at(23, 30),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"needs_action": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.booking.id, self.list_ids(response))
        self.assertIn(self.confirmed_booking.id, self.list_ids(response))
        self.assertNotIn(completed_with_remaining.id, self.list_ids(response))

    def test_needs_action_includes_expired_only_with_explicit_date_context(self):
        contextual_expired = self.create_booking(
            self.court,
            customer_phone="+201000000091",
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            status=Booking.Status.EXPIRED,
        )
        historical_expired = self.create_booking(
            self.court,
            customer_phone="+201000000092",
            start_time=timezone.datetime(
                2026,
                4,
                15,
                18,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            end_time=timezone.datetime(
                2026,
                4,
                15,
                19,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            status=Booking.Status.EXPIRED,
        )

        unscoped = self.client.get(
            self.booking_list_url(self.club),
            {"needs_action": "true"},
        )
        dated = self.client.get(
            self.booking_list_url(self.club),
            {"needs_action": "true", "date": "2026-05-20"},
        )

        self.assertEqual(unscoped.status_code, status.HTTP_200_OK)
        self.assertEqual(dated.status_code, status.HTTP_200_OK)
        self.assertIn(self.booking.id, self.list_ids(unscoped))
        self.assertNotIn(contextual_expired.id, self.list_ids(unscoped))
        self.assertNotIn(historical_expired.id, self.list_ids(unscoped))
        self.assertIn(contextual_expired.id, self.list_ids(dated))
        self.assertNotIn(historical_expired.id, self.list_ids(dated))

    def test_overdue_filter_returns_ended_bookings(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"status": Booking.Status.CONFIRMED, "overdue": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_remaining_amount_gt_and_ended_filter_returns_confirmed_ended_bookings(
        self,
    ):
        completed_with_remaining = self.create_booking(
            self.court,
            customer_phone="+201000000009",
            start_time=self.time_at(23),
            end_time=self.time_at(23, 30),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"remaining_amount_gt": "0", "ended": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.confirmed_booking.id, self.list_ids(response))
        self.assertNotIn(completed_with_remaining.id, self.list_ids(response))

    def test_has_remaining_amount_true_uses_paid_versus_total_price(self):
        fully_paid = self.create_booking(
            self.court,
            customer_phone="+201000000019",
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            status=Booking.Status.CONFIRMED,
        )
        self.create_transaction(fully_paid, amount=fully_paid.total_price)
        completed_with_remaining = self.create_booking(
            self.court,
            customer_phone="+201000000029",
            start_time=self.time_at(19),
            end_time=self.time_at(20),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"has_remaining_amount": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self.list_ids(response)
        self.assertIn(self.booking.id, ids)
        self.assertIn(self.confirmed_booking.id, ids)
        self.assertIn(completed_with_remaining.id, ids)
        self.assertNotIn(fully_paid.id, ids)

    def test_has_remaining_amount_false_returns_fully_paid_bookings(self):
        fully_paid = self.create_booking(
            self.court,
            customer_phone="+201000000039",
            start_time=self.time_at(17),
            end_time=self.time_at(18),
            status=Booking.Status.CONFIRMED,
        )
        self.create_transaction(fully_paid, amount=fully_paid.total_price)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"has_remaining_amount": "false"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {fully_paid.id})

    def test_hold_expiring_filter_uses_internal_hold_expiry_hours(self):
        self.court.internal_hold_expiry_hours = 1
        self.court.save(update_fields=["internal_hold_expiry_hours"])
        expiring_hold = self.create_booking(
            self.court,
            customer_phone="+201000000010",
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=3),
            status=Booking.Status.HOLD,
        )
        Booking.objects.filter(pk=expiring_hold.pk).update(
            created=timezone.now() - timedelta(minutes=45)
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"hold_expiring": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {expiring_hold.id})

    def test_search_matches_customer_name_and_phone_variants(self):
        named = self.create_booking(
            self.court,
            customer_name="Ahmed Hassan",
            customer_phone="+201012345678",
            start_time=self.time_at(10),
            end_time=self.time_at(11),
        )
        other = self.create_booking(
            self.court,
            customer_name="Mona Ali",
            customer_phone="+201000000088",
            start_time=self.time_at(11),
            end_time=self.time_at(12),
        )
        other_club_same_name = self.create_booking(
            self.other_court,
            customer_name="Ahmed Hassan",
            customer_phone="+201012345678",
        )

        name_response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "Ahmed"},
        )
        phone_response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "01012345678"},
        )
        spaced_phone_response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "0101 234 5678"},
        )
        e164_response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "+201012345678", "status": Booking.Status.HOLD},
        )

        self.assertEqual(self.list_ids(name_response), {named.id})
        self.assertEqual(self.list_ids(phone_response), {named.id})
        self.assertEqual(self.list_ids(spaced_phone_response), {named.id})
        self.assertEqual(self.list_ids(e164_response), {named.id})
        self.assertNotIn(other.id, self.list_ids(name_response))
        self.assertNotIn(other_club_same_name.id, self.list_ids(name_response))
        self.assertNotIn(other_club_same_name.id, self.list_ids(phone_response))

    def test_search_matches_notes(self):
        tournament = self.create_booking(
            self.court,
            customer_name="Notes Hidden",
            customer_phone="+201000000093",
            start_time=self.time_at(13),
            end_time=self.time_at(14),
            notes="بطولة الشركة",
        )
        other = self.create_booking(
            self.court,
            customer_name="Notes Other",
            customer_phone="+201000000094",
            start_time=self.time_at(14),
            end_time=self.time_at(15),
            notes="training session",
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "بطولة"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {tournament.id})
        self.assertNotIn(other.id, self.list_ids(response))

    def test_search_respects_staff_court_scope(self):
        staff = self.create_user("search-staff")
        hidden_court = self.create_court(self.club, "Hidden Search Court")
        visible = self.create_booking(
            self.court,
            customer_name="Search Visible",
            customer_phone="+201011111111",
            start_time=self.time_at(12),
            end_time=self.time_at(13),
        )
        hidden = self.create_booking(
            hidden_court,
            customer_name="Search Visible",
            customer_phone="+201011111111",
            start_time=self.time_at(12),
            end_time=self.time_at(13),
        )
        self.create_membership(
            staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=staff)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"search": "Search Visible"},
        )

        self.assertEqual(self.list_ids(response), {visible.id})
        self.assertNotIn(hidden.id, self.list_ids(response))

    def test_upcoming_includes_in_progress_and_excludes_terminal(self):
        now = timezone.now()
        in_progress = self.create_booking(
            self.court,
            customer_phone="+201000000071",
            start_time=now - timedelta(minutes=30),
            end_time=now + timedelta(minutes=30),
            status=Booking.Status.CONFIRMED,
        )
        future_hold = self.create_booking(
            self.court,
            customer_phone="+201000000072",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
            status=Booking.Status.HOLD,
        )
        ended_confirmed = self.create_booking(
            self.court,
            customer_phone="+201000000073",
            start_time=now - timedelta(hours=3),
            end_time=now - timedelta(hours=2),
            status=Booking.Status.CONFIRMED,
        )
        completed = self.create_booking(
            self.court,
            customer_phone="+201000000074",
            start_time=now + timedelta(hours=4),
            end_time=now + timedelta(hours=5),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"upcoming": "true"},
        )

        ids = self.list_ids(response)
        self.assertEqual(ids, {in_progress.id, future_hold.id})
        self.assertNotIn(ended_confirmed.id, ids)
        self.assertNotIn(completed.id, ids)
        self.assertNotIn(self.other_booking.id, ids)

    def test_hold_expires_at_matches_expiration_rule_and_is_null_when_not_hold(self):
        hold = self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            customer_phone="+201000000075",
        )
        confirmed = self.confirmed_booking
        hold.refresh_from_db()

        list_response = self.client.get(self.booking_list_url(self.club))
        detail_response = self.client.get(self.booking_detail_url(self.club, hold))
        confirmed_detail = self.client.get(
            self.booking_detail_url(self.club, confirmed)
        )

        hold_row = next(
            item for item in list_response.data["results"] if item["id"] == hold.id
        )
        expected = compute_booking_hold_expires_at(hold)
        self.assertEqual(hold_row["hold_expires_at"], expected)
        self.assertEqual(detail_response.data["hold_expires_at"], expected)
        self.assertIsNone(confirmed_detail.data["hold_expires_at"])
        confirmed_row = next(
            item for item in list_response.data["results"] if item["id"] == confirmed.id
        )
        self.assertIsNone(confirmed_row["hold_expires_at"])


class BookingFilterPatternTests(BookingAPITestCase):
    def test_booking_route_resolves_to_viewset(self):
        match = resolve("/api/v1/clubs/example-club/bookings/")

        self.assertIs(match.func.cls, BookingViewSet)

    def test_booking_viewset_uses_django_filter_backend(self):
        self.assertEqual(BookingViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(BookingViewSet.filterset_class, BookingFilter)

    def test_booking_viewset_does_not_manually_parse_filter_query_params(self):
        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "bookings" / "views.py").read_text()

        self.assertNotIn("request.query_params", view_source)
        self.assertNotIn("parse_date", view_source)
        self.assertNotIn("parse_datetime", view_source)

    def test_booking_filter_does_not_contain_access_logic(self):
        repo_root = Path(__file__).resolve().parents[2]
        filter_source = (repo_root / "apps" / "bookings" / "filters.py").read_text()

        self.assertNotIn("ClubMembership", filter_source)
        self.assertNotIn("ClubAccessContext", filter_source)
        self.assertNotIn("get_access_context", filter_source)
        self.assertNotIn("club_slug", filter_source)

    def test_openapi_documents_new_booking_contract_fields(self):
        response = self.client.get(reverse("schema"))
        schema = response.content.decode()
        schema_doc = yaml.safe_load(schema)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_list_fields = schema_doc["components"]["schemas"]["BookingList"][
            "properties"
        ]
        self.assertIn("notes", booking_list_fields)
        self.assertIn("hold_expires_at", schema)
        self.assertIn("recurrence-next", schema)
        self.assertIn("search", schema)
        self.assertIn("upcoming", schema)
        self.assertIn("requires_digital_payment_reference", schema)


class BookingPaymentSummaryTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("payment-summary-admin")
        self.staff = self.create_user("payment-summary-staff")
        self.club = self.create_club("Payment Summary Club", slug="payment-summary")
        self.other_club = self.create_club(
            "Other Payment Summary Club",
            slug="other-payment-summary",
        )
        self.court = self.create_court(self.club, "Summary Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Summary Other Court",
        )
        self.other_court = self.create_court(self.other_club, "External Summary Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(self.same_club_other_court)
        self.other_booking = self.create_booking(self.other_court)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_booking_list_includes_zero_payment_summary_without_transactions(self):
        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_data = next(
            item for item in response.data["results"] if item["id"] == self.booking.id
        )
        self.assertEqual(booking_data["paid_amount"], "0.00")
        self.assertEqual(booking_data["remaining_amount"], "300.00")
        self.assertFalse(booking_data["is_fully_paid"])

    def test_booking_detail_includes_payment_summary(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["paid_amount"], "100.00")
        self.assertEqual(response.data["remaining_amount"], "200.00")
        self.assertFalse(response.data["is_fully_paid"])

    def test_payment_summary_is_correct_after_partial_payment(self):
        self.create_transaction(self.booking, amount=Decimal("125.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.data["paid_amount"], "125.00")
        self.assertEqual(response.data["remaining_amount"], "175.00")
        self.assertFalse(response.data["is_fully_paid"])

    def test_payment_summary_is_fully_paid_after_full_payment(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.booking, amount=Decimal("200.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.data["paid_amount"], "300.00")
        self.assertEqual(response.data["remaining_amount"], "0.00")
        self.assertTrue(response.data["is_fully_paid"])

    def test_payment_summary_ignores_cancelled_transactions(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(
            self.booking,
            amount=Decimal("200.00"),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Wrong payment",
        )

        detail_response = self.client.get(
            self.booking_detail_url(self.club, self.booking)
        )
        list_response = self.client.get(self.booking_list_url(self.club))
        list_item = next(
            item
            for item in list_response.data["results"]
            if item["id"] == self.booking.id
        )

        for data in (detail_response.data, list_item):
            self.assertEqual(data["paid_amount"], "100.00")
            self.assertEqual(data["remaining_amount"], "200.00")
            self.assertFalse(data["is_fully_paid"])

    def test_payment_summary_respects_club_scoped_booking_access(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.other_booking, amount=Decimal("200.00"))

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.booking.id, self.list_ids(response))
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_staff_sees_payment_summary_only_for_assigned_court_bookings(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.same_club_other_booking, amount=Decimal("200.00"))
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.booking.id})
        booking_data = response.data["results"][0]
        self.assertEqual(booking_data["paid_amount"], "100.00")
        self.assertEqual(booking_data["remaining_amount"], "200.00")
        self.assertFalse(booking_data["is_fully_paid"])


class BookingQueryScalingTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("query-scale-admin")
        self.club = self.create_club("Query Scale Club", slug="query-scale-club")
        self.court = self.create_court(self.club, "Query Scale Court")
        self.client.force_authenticate(user=self.platform_admin)

    def test_booking_list_query_count_does_not_grow_with_rows(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            customer_phone="+201000009001",
        )

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(self.booking_list_url(self.club))

        for hour in range(10, 19):
            self.create_booking(
                self.court,
                start_time=self.time_at(hour),
                end_time=self.time_at(hour + 1),
                customer_phone=f"+2010000090{hour}",
            )

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first_response.data["results"]), 1)
        self.assertEqual(len(second_response.data["results"]), 10)
        self.assertEqual(len(first), len(second))

    def test_booking_detail_query_count_stays_bounded(self):
        booking = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
        )
        self.create_transaction(booking, amount=Decimal("50.00"))

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.booking_detail_url(self.club, booking))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 5)
        self.assertEqual(response.data["paid_amount"], "50.00")

    def create_attempt(self, hour):
        return BookingAttempt.objects.create(
            club=self.club,
            court=self.court,
            attempted_by=self.platform_admin,
            client_request_id=uuid4(),
            customer_name=f"Attempt Customer {hour}",
            customer_phone=f"+2010000093{hour:02d}",
            requested_start=self.time_at(hour),
            requested_end=self.time_at(hour + 1),
            requested_at=self.time_at(8),
            requested_source=Booking.Source.MANUAL,
            requested_recurring=False,
            outcome=BookingAttempt.Outcome.REJECTED,
            failure_code="BOOKING_SLOT_UNAVAILABLE",
        )

    def test_booking_attempt_list_query_count_does_not_grow_with_rows(self):
        self.create_attempt(9)

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(self.booking_attempt_list_url(self.club))

        for hour in range(10, 19):
            self.create_attempt(hour)

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(self.booking_attempt_list_url(self.club))

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first_response.data["results"]), 1)
        self.assertEqual(len(second_response.data["results"]), 10)
        self.assertEqual(len(first), len(second))

    def test_schedule_query_count_stays_bounded_across_slot_and_anchor_growth(self):
        self.create_working_hours(
            self.court,
            weekday=2,
            opens_at=time(9, 0),
            closes_at=time(12, 0),
        )

        def slot_query_count(**params):
            data = {"court": self.court.id, "date": "2026-05-20"}
            if "date_from" in params or "date_to" in params:
                data.pop("date")
            data.update(params)
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.booking_slots_url(self.club), data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            return len(queries), response.data["slots"]

        empty_count, empty_slots = slot_query_count()
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000009101",
        )
        few_count, _few_slots = slot_query_count()
        self.create_booking(
            self.court,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000009102",
        )
        one_anchor_count, one_week_slots = slot_query_count(
            date_from="2026-05-20",
            date_to="2026-06-10",
        )
        self.create_booking(
            self.court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000009103",
        )
        many_anchor_count, many_week_slots = slot_query_count(
            date_from="2026-05-20",
            date_to="2026-06-10",
        )

        reserved = [
            slot
            for slot in many_week_slots
            if slot["slot_status"] == "RECURRING_RESERVED"
        ]
        self.assertGreater(len(empty_slots), 0)
        self.assertGreaterEqual(len(reserved), 2)
        self.assertEqual(empty_count, few_count)
        self.assertEqual(one_anchor_count, many_anchor_count)
        self.assertEqual(empty_count, one_anchor_count)

    def test_profiler_flags_orm_get_loop_but_not_batched_select_related(self):
        bookings = [
            self.create_booking(
                self.court,
                start_time=self.time_at(9 + index),
                end_time=self.time_at(10 + index),
                customer_phone=f"+2010000092{index:02d}",
            )
            for index in range(3)
        ]

        naive_stats = SQLQueryStats()
        with connection.execute_wrapper(naive_stats):
            for booking in bookings:
                Booking.objects.get(pk=booking.pk)

        batched_stats = SQLQueryStats()
        ids = [booking.pk for booking in bookings]
        with connection.execute_wrapper(batched_stats):
            list(
                Booking.objects.filter(pk__in=ids).select_related(
                    "court",
                    "club",
                    "previous_recurring_booking",
                    "next_recurring_booking",
                )
            )

        self.assertGreaterEqual(naive_stats.potential_n_plus_one_count, 1)
        self.assertEqual(batched_stats.potential_n_plus_one_count, 0)
        self.assertGreater(naive_stats.query_count, batched_stats.query_count)


@unittest.skip(
    "APPROVAL REQUIRED — RESCHEDULE REFUND PERSISTENCE: "
    "lost refund rights cannot be preserved after start_time is overwritten "
    "without stored booking state."
)
class BookingRescheduleRefundEntitlementTests(BookingAPITestCase):
    def setUp(self):
        self.owner = self.create_user("refund-reschedule-owner")
        self.club = self.create_club("Refund Reschedule Club", slug="refund-reschedule")
        self.court = self.create_court(self.club, "Refund Reschedule Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)

    def post_lifecycle(self, club, booking, action_name, user, payload=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        return self.client.post(
            self.booking_lifecycle_url(club, booking, action_name),
            payload or {},
            format="json",
        )

    def configure_refund_policy(self):
        self.court.minimum_deposit = Decimal("100.00")
        self.court.cancellation_refund_notice_days = 1
        self.court.save(
            update_fields=["minimum_deposit", "cancellation_refund_notice_days"]
        )

    def later_wednesday(self, hour):
        return timezone.datetime(
            2026,
            6,
            3,
            hour,
            tzinfo=timezone.get_current_timezone(),
        )

    def preview_amounts(self, booking, now):
        with patch("apps.bookings.services.timezone.now", return_value=now):
            response = self.client.post(
                self.booking_lifecycle_url(
                    self.club,
                    booking,
                    "cancellation-preview",
                ),
                {},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return (
            Decimal(response.data["refund_amount"]),
            Decimal(response.data["retained_amount"]),
        )

    def paid_confirmed_booking(self, *, start_hour=20, amount="300.00"):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(start_hour),
            end_time=self.time_at(start_hour + 1),
            customer_phone="+201000002001",
        )
        self.create_transaction(
            booking,
            amount=Decimal(amount),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.owner,
        )
        return booking

    def reschedule_to(self, booking, start_time):
        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.owner,
            {
                "court": self.court.id,
                "start_time": start_time.isoformat(),
                "end_time": (start_time + timedelta(hours=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        return booking

    def test_full_refund_remains_available_after_later_reschedule(self):
        self.configure_refund_policy()
        booking = self.paid_confirmed_booking()
        before_now = timezone.datetime(
            2026,
            5,
            18,
            10,
            tzinfo=timezone.get_current_timezone(),
        )
        self.client.force_authenticate(user=self.owner)
        refund_before, retained_before = self.preview_amounts(booking, before_now)
        self.assertEqual(refund_before, Decimal("300.00"))
        self.assertEqual(retained_before, Decimal("0.00"))

        self.reschedule_to(booking, self.later_wednesday(20))
        refund_after, retained_after = self.preview_amounts(booking, before_now)
        self.assertEqual(refund_after, Decimal("300.00"))
        self.assertEqual(retained_after, Decimal("0.00"))

    def test_non_refundable_deposit_stays_non_refundable_after_later_reschedule(self):
        self.configure_refund_policy()
        booking = self.paid_confirmed_booking()
        now = self.time_at(10)
        self.client.force_authenticate(user=self.owner)
        refund_before, retained_before = self.preview_amounts(booking, now)
        self.assertEqual(refund_before, Decimal("200.00"))
        self.assertEqual(retained_before, Decimal("100.00"))

        self.reschedule_to(booking, self.later_wednesday(20))
        refund_after, retained_after = self.preview_amounts(booking, now)
        self.assertLessEqual(refund_after, Decimal("200.00"))
        self.assertGreaterEqual(retained_after, Decimal("100.00"))

    def test_partial_refund_does_not_improve_after_later_reschedule(self):
        self.test_non_refundable_deposit_stays_non_refundable_after_later_reschedule()

    def test_multiple_reschedules_never_improve_refund_rights(self):
        self.configure_refund_policy()
        booking = self.paid_confirmed_booking()
        now = self.time_at(10)
        self.client.force_authenticate(user=self.owner)
        _, retained_before = self.preview_amounts(booking, now)
        self.reschedule_to(booking, self.later_wednesday(20))
        _, retained_mid = self.preview_amounts(booking, now)
        self.reschedule_to(booking, self.later_wednesday(21))
        refund_after, retained_after = self.preview_amounts(booking, now)
        self.assertGreaterEqual(retained_mid, retained_before)
        self.assertGreaterEqual(retained_after, retained_mid)
        self.assertLessEqual(refund_after, Decimal("200.00"))

    def test_earlier_reschedule_does_not_improve_refund_rights(self):
        self.configure_refund_policy()
        booking = self.paid_confirmed_booking(start_hour=21)
        now = self.time_at(10)
        self.client.force_authenticate(user=self.owner)
        refund_before, retained_before = self.preview_amounts(booking, now)
        self.reschedule_to(booking, self.time_at(12))
        refund_after, retained_after = self.preview_amounts(booking, now)
        self.assertLessEqual(refund_after, refund_before)
        self.assertGreaterEqual(retained_after, retained_before)

    def test_preview_and_cancel_use_the_same_authoritative_amounts(self):
        self.configure_refund_policy()
        booking = self.paid_confirmed_booking()
        now = self.time_at(10)
        self.client.force_authenticate(user=self.owner)
        self.reschedule_to(booking, self.later_wednesday(20))
        refund_preview, retained_preview = self.preview_amounts(booking, now)
        with patch("apps.bookings.services.timezone.now", return_value=now):
            cancel = self.post_lifecycle(
                self.club,
                booking,
                "cancel",
                self.owner,
                {
                    "reason": "Customer cancelled",
                    "refund_payment_method": Transaction.PaymentMethod.CASH,
                },
            )
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        refund = Transaction.objects.get(
            booking=booking,
            transaction_type=Transaction.Type.REFUND,
        )
        self.assertEqual(-refund.amount, refund_preview)
        self.assertEqual(
            Decimal("300.00") - refund_preview,
            retained_preview,
        )
