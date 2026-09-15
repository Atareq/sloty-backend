"""
Integration tests for Offline Safety, Reconnect Behavior, Idempotency,
and Financial Safety.
"""

from datetime import time
from decimal import Decimal
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.players.models import ClubPlayer, PlayerProfile
from apps.profiles.models import Profile, StaffProfile
from apps.transactions.models import Transaction
from tests.booking_factories import persist_booking


class BookingOfflineSafetyTests(APITestCase):
    password = "Test-password-1234!"

    def setUp(self):
        self.club = Club.objects.create(
            name="Offline Safety Club",
            slug="offline-safety-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court = Court.objects.create(
            club=self.club,
            name="Offline Court",
            default_price=Decimal("200.00"),
            slot_duration_minutes=60,
        )
        for weekday in range(7):
            wh = CourtWorkingHour.objects.create(
                court=self.court,
                weekday=weekday,
            )
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=wh,
                starts_at=time(6, 0),
                ends_at=time(0, 0),
                price=Decimal("200.00"),
            )
        self.staff = User.objects.create_user(
            username="offline-staff",
            password=self.password,
        )
        profile = Profile.objects.create(user=self.staff, role=Profile.Role.STAFF)
        self.staff_profile = StaffProfile.objects.create(
            profile=profile, court=self.court
        )
        self.client.force_authenticate(user=self.staff)

    def booking_create_url(self):
        return reverse("club-booking-list", kwargs={"club_slug": self.club.slug})

    def time_at(self, day_offset: int, hour: int):
        return timezone.datetime(
            2026,
            5,
            20 + day_offset,
            hour,
            0,
            tzinfo=timezone.get_current_timezone(),
        )

    def test_booking_reconnect_replay_with_same_client_request_id_returns_200_ok(self):
        client_request_id = str(uuid4())
        start = self.time_at(2, 18)
        end = start + timezone.timedelta(hours=1)
        payload = {
            "court": self.court.id,
            "client_request_id": client_request_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "customer_name": "Replay Customer",
            "customer_phone": "+201088776655",
            "notes": "First offline queue sync",
        }

        # First request on reconnect -> 201 Created
        res1 = self.client.post(self.booking_create_url(), payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        booking_id = res1.data["id"]
        self.assertEqual(Booking.objects.filter(club=self.club).count(), 1)

        # Reconnect retry with exact same client_request_id & payload
        # -> 200 OK with existing booking
        res2 = self.client.post(self.booking_create_url(), payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["id"], booking_id)
        self.assertEqual(Booking.objects.filter(club=self.club).count(), 1)

    def test_booking_reconnect_mismatched_payload_returns_409_conflict(self):
        client_request_id = str(uuid4())
        start = self.time_at(3, 19)
        end = start + timezone.timedelta(hours=1)
        payload = {
            "court": self.court.id,
            "client_request_id": client_request_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "customer_name": "Conflict Customer",
            "customer_phone": "+201088776644",
        }

        # First request succeeds -> 201 Created
        res1 = self.client.post(self.booking_create_url(), payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Replay with same client_request_id but different customer phone
        # -> 409 Conflict
        mismatched_payload = dict(payload)
        mismatched_payload["customer_phone"] = "+201011119999"

        res2 = self.client.post(
            self.booking_create_url(), mismatched_payload, format="json"
        )
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data["code"], "BOOKING_CLIENT_REQUEST_MISMATCH")

    def test_booking_offline_walk_in_resolves_identity_on_reconnect(self):
        phone = "+201022334455"
        name = "Walkin Sync Player"
        start = self.time_at(4, 20)
        end = start + timezone.timedelta(hours=1)

        payload = {
            "court": self.court.id,
            "client_request_id": str(uuid4()),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "customer_name": name,
            "customer_phone": phone,
        }

        # Reconnect sync creates PlayerProfile and ClubPlayer version
        res = self.client.post(self.booking_create_url(), payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        profile = PlayerProfile.objects.filter(phone_number=phone).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.full_name, name)

        club_player = ClubPlayer.objects.filter(
            club=self.club, player_profile=profile
        ).first()
        self.assertIsNotNone(club_player)
        self.assertEqual(club_player.display_name, name)
        self.assertTrue(club_player.is_current_version)

        # Booking points to the resolved ClubPlayer
        booking = Booking.objects.get(id=res.data["id"])
        self.assertEqual(booking.club_player, club_player)

    def test_revoked_staff_access_before_reconnect_rejects_booking_sync(self):
        start = self.time_at(5, 17)
        end = start + timezone.timedelta(hours=1)
        payload = {
            "court": self.court.id,
            "client_request_id": str(uuid4()),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "customer_name": "Revoked Staff Customer",
            "customer_phone": "+201033445566",
        }

        # Removing the Profile scope revokes access immediately.
        self.staff_profile.delete()

        # Reconnect sync attempt is rejected immediately -> 403 CLUB_ACCESS_REVOKED
        res = self.client.post(self.booking_create_url(), payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data["code"], "CLUB_ACCESS_REVOKED")

        # Zero bookings created
        self.assertEqual(Booking.objects.filter(club=self.club).count(), 0)

    def test_financial_mutation_cannot_bypass_server_validation(self):
        start = self.time_at(6, 18)
        booking = persist_booking(
            self.court,
            start_time=start,
            end_time=start + timezone.timedelta(hours=1),
            total_price=Decimal("300.00"),
            created_by=self.staff,
            customer_name="Financial Safety Customer",
            customer_phone="+201044556677",
        )
        tx_url = reverse("club-transaction-list", kwargs={"club_slug": self.club.slug})

        # 1. Payment exceeding remaining booking amount is rejected by server
        overpay_payload = {
            "court": self.court.id,
            "booking": booking.id,
            "amount": "500.00",
            "payment_method": "CASH",
            "client_request_id": str(uuid4()),
        }
        res_over = self.client.post(tx_url, overpay_payload, format="json")
        self.assertEqual(res_over.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_over.data["code"], "VALIDATION_ERROR")
        self.assertIn("amount", res_over.data["field_errors"])
        self.assertIn("remaining booking amount", res_over.data["message"])

        # 2. Valid payment with client_request_id succeeds -> 201
        tx_id = str(uuid4())
        valid_payload = {
            "court": self.court.id,
            "booking": booking.id,
            "amount": "200.00",
            "payment_method": "CASH",
            "client_request_id": tx_id,
        }
        res_valid = self.client.post(tx_url, valid_payload, format="json")
        self.assertEqual(res_valid.status_code, status.HTTP_201_CREATED)
        first_tx_id = res_valid.data["id"]

        # 3. Idempotent replay with same client_request_id -> 200 OK
        # without duplicate payment
        res_replay = self.client.post(tx_url, valid_payload, format="json")
        self.assertEqual(res_replay.status_code, status.HTTP_200_OK)
        self.assertEqual(res_replay.data["id"], first_tx_id)
        self.assertEqual(Transaction.objects.filter(booking=booking).count(), 1)
