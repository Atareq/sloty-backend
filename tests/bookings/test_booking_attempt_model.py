import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.identity import (
    booking_customer_display_name,
    booking_customer_display_phone,
)
from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.services import blocking_booking_queryset
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction
from tests.booking_factories import persist_booking


class BookingAttemptModelTests(TestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_club(self, name: str, slug: str) -> Club:
        return Club.objects.create(
            name=name,
            slug=slug,
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_court(self, club: Club, name: str) -> Court:
        return Court.objects.create(
            club=club,
            name=name,
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )

    def time_at(self, hour: int):
        return timezone.datetime(
            2026,
            5,
            20,
            hour,
            0,
            tzinfo=timezone.get_current_timezone(),
        )

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        extra_fields.setdefault("start_time", self.time_at(20))
        extra_fields.setdefault("end_time", self.time_at(21))
        extra_fields.setdefault("customer_name", "Accepted Customer")
        extra_fields.setdefault("customer_phone", "+201000000001")
        return persist_booking(court, **extra_fields)

    def create_rejected_attempt(self, court: Court, attempted_by: User, **extra_fields):
        data = {
            "club": court.club,
            "court": court,
            "attempted_by": attempted_by,
            "client_request_id": uuid.uuid4(),
            "customer_name": "Rejected Customer",
            "customer_phone": "+201000000002",
            "notes": "Customer wanted the busy slot",
            "requested_start": self.time_at(20),
            "requested_end": self.time_at(21),
            "requested_at": self.time_at(19),
            "requested_recurring": False,
            "outcome": BookingAttempt.Outcome.REJECTED,
            "failure_code": "BOOKING_SLOT_UNAVAILABLE",
            "failure_details": {"conflict_type": "BOOKING"},
        }
        data.update(extra_fields)
        return BookingAttempt.objects.create(**data)

    def setUp(self):
        self.club = self.create_club("Attempt Club", "attempt-club")
        self.other_club = self.create_club("Other Attempt Club", "other-attempt")
        self.court = self.create_court(self.club, "Attempt Court")
        self.other_court = self.create_court(self.other_club, "Other Court")
        self.staff = self.create_user("attempt-staff")
        self.create_user("other-staff")
        self.membership = ClubMembership.objects.create(
            club=self.club,
            court=self.court,
            user=self.staff,
            role=ClubMembership.Role.STAFF,
        )

    def test_rejected_attempt_preserves_original_business_intent(self):
        client_request_id = uuid.uuid4()
        attempt = self.create_rejected_attempt(
            self.court,
            self.staff,
            client_request_id=client_request_id,
            customer_name="Ahmed Hassan",
            customer_phone="+201012345678",
            notes="Offline note",
            requested_recurring=True,
            requested_source=Booking.Source.RECURRING,
            requested_at=self.time_at(18),
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.club, self.club)
        self.assertEqual(attempt.court, self.court)
        self.assertEqual(attempt.attempted_by, self.staff)
        self.assertEqual(attempt.client_request_id, client_request_id)
        self.assertEqual(attempt.customer_name, "Ahmed Hassan")
        self.assertEqual(str(attempt.customer_phone), "+201012345678")
        self.assertEqual(attempt.notes, "Offline note")
        self.assertEqual(attempt.requested_start, self.time_at(20))
        self.assertEqual(attempt.requested_end, self.time_at(21))
        self.assertEqual(attempt.requested_at, self.time_at(18))
        self.assertEqual(attempt.requested_source, Booking.Source.RECURRING)
        self.assertTrue(attempt.requested_recurring)
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.failure_code, "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(attempt.failure_details, {"conflict_type": "BOOKING"})
        self.assertEqual(attempt.resolution, BookingAttempt.Resolution.UNRESOLVED)
        self.assertIsNotNone(attempt.created)
        self.assertIsNotNone(attempt.modified)
        self.assertEqual(self.membership.club, attempt.club)
        self.assertEqual(self.membership.court, attempt.court)
        self.assertEqual(self.membership.user, attempt.attempted_by)

    def test_successful_attempt_can_reference_resulting_booking(self):
        booking = self.create_booking(self.court, created_by=self.staff)
        attempt = BookingAttempt.objects.create(
            club=self.club,
            court=self.court,
            attempted_by=self.staff,
            booking=booking,
            client_request_id=uuid.uuid4(),
            customer_name=booking_customer_display_name(booking),
            customer_phone=booking_customer_display_phone(booking),
            requested_start=booking.start_time,
            requested_end=booking.end_time,
            requested_at=booking.created,
            requested_source=booking.source,
            requested_recurring=False,
            outcome=BookingAttempt.Outcome.SUCCESS,
            resolution=BookingAttempt.Resolution.RESOLVED,
        )

        self.assertEqual(attempt.booking, booking)
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.failure_code, "")
        self.assertIn(attempt, booking.attempts.all())

    def test_rejected_attempt_does_not_create_fake_booking_or_financial_rows(self):
        self.assertEqual(Booking.objects.count(), 0)

        attempt = self.create_rejected_attempt(self.court, self.staff)

        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.REJECTED)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(
            blocking_booking_queryset(
                self.court,
                attempt.requested_start,
                attempt.requested_end,
            ).exists()
        )
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Settlement.objects.count(), 0)

    def test_attempt_preserves_original_values_when_booking_changes_later(self):
        booking = self.create_booking(self.court, created_by=self.staff)
        attempt = BookingAttempt.objects.create(
            club=self.club,
            court=self.court,
            attempted_by=self.staff,
            booking=booking,
            client_request_id=uuid.uuid4(),
            customer_name="Original Customer",
            customer_phone="+201000000003",
            notes="Original note",
            requested_start=booking.start_time,
            requested_end=booking.end_time,
            requested_at=booking.created,
            requested_source=booking.source,
            requested_recurring=False,
            outcome=BookingAttempt.Outcome.SUCCESS,
            resolution=BookingAttempt.Resolution.RESOLVED,
        )

        booking.start_time = booking.start_time + timedelta(hours=2)
        booking.end_time = booking.end_time + timedelta(hours=2)
        booking.notes = "Changed note"
        booking.save(update_fields=["start_time", "end_time", "notes"])

        attempt.refresh_from_db()
        self.assertEqual(attempt.customer_name, "Original Customer")
        self.assertEqual(str(attempt.customer_phone), "+201000000003")
        self.assertEqual(attempt.notes, "Original note")
        self.assertEqual(attempt.requested_start, self.time_at(20))
        self.assertEqual(attempt.requested_end, self.time_at(21))

    def test_full_clean_rejects_cross_club_court_and_booking_mismatches(self):
        other_booking = self.create_booking(self.other_court)

        with self.assertRaises(ValidationError) as court_error:
            BookingAttempt(
                club=self.club,
                court=self.other_court,
                attempted_by=self.staff,
                client_request_id=uuid.uuid4(),
                customer_name="Cross Club Customer",
                customer_phone="+201000000004",
                requested_start=self.time_at(20),
                requested_end=self.time_at(21),
                requested_at=self.time_at(19),
                outcome=BookingAttempt.Outcome.REJECTED,
                failure_code="BOOKING_SLOT_UNAVAILABLE",
            ).full_clean()
        self.assertIn("court", court_error.exception.message_dict)

        with self.assertRaises(ValidationError) as booking_error:
            BookingAttempt(
                club=self.club,
                court=self.court,
                attempted_by=self.staff,
                booking=other_booking,
                client_request_id=uuid.uuid4(),
                customer_name="Cross Booking Customer",
                customer_phone="+201000000005",
                requested_start=self.time_at(20),
                requested_end=self.time_at(21),
                requested_at=self.time_at(19),
                outcome=BookingAttempt.Outcome.SUCCESS,
            ).full_clean()
        self.assertIn("booking", booking_error.exception.message_dict)

    def test_full_clean_rejects_accepted_attempt_source_mismatch(self):
        booking = self.create_booking(
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
        )

        with self.assertRaises(ValidationError) as error:
            BookingAttempt(
                club=self.club,
                court=self.court,
                attempted_by=self.staff,
                booking=booking,
                client_request_id=uuid.uuid4(),
                customer_name="Source Customer",
                customer_phone="+201000000007",
                requested_start=self.time_at(20),
                requested_end=self.time_at(21),
                requested_at=self.time_at(19),
                requested_source=Booking.Source.MANUAL,
                outcome=BookingAttempt.Outcome.SUCCESS,
            ).full_clean()

        self.assertIn("requested_source", error.exception.message_dict)

    def test_client_request_id_is_unique_per_club_for_attempts(self):
        client_request_id = uuid.uuid4()
        self.create_rejected_attempt(
            self.court,
            self.staff,
            client_request_id=client_request_id,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_rejected_attempt(
                    self.court,
                    self.staff,
                    client_request_id=client_request_id,
                )

        other_staff = self.create_user("other-attempt-staff")
        other_attempt = self.create_rejected_attempt(
            self.other_court,
            other_staff,
            client_request_id=client_request_id,
        )
        self.assertEqual(other_attempt.club, self.other_club)

    def test_attempts_without_client_request_id_are_allowed(self):
        first = self.create_rejected_attempt(
            self.court,
            self.staff,
            client_request_id=None,
            requested_start=self.time_at(20),
            requested_end=self.time_at(21),
        )
        second = self.create_rejected_attempt(
            self.court,
            self.staff,
            client_request_id=None,
            requested_start=self.time_at(21),
            requested_end=self.time_at(22),
        )

        self.assertIsNone(first.client_request_id)
        self.assertIsNone(second.client_request_id)
        self.assertEqual(BookingAttempt.objects.count(), 2)

    def test_database_constraints_enforce_attempt_outcome_shape(self):
        valid_payload = {
            "club": self.club,
            "court": self.court,
            "attempted_by": self.staff,
            "client_request_id": uuid.uuid4(),
            "customer_name": "Constraint Customer",
            "customer_phone": "+201000000006",
            "requested_start": self.time_at(20),
            "requested_end": self.time_at(21),
            "requested_at": self.time_at(19),
        }

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BookingAttempt.objects.create(
                    **valid_payload,
                    outcome=BookingAttempt.Outcome.SUCCESS,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                payload = {
                    **valid_payload,
                    "client_request_id": uuid.uuid4(),
                }
                BookingAttempt.objects.create(
                    **payload,
                    outcome=BookingAttempt.Outcome.REJECTED,
                )

        booking = self.create_booking(self.court)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                payload = {
                    **valid_payload,
                    "booking": booking,
                    "client_request_id": uuid.uuid4(),
                }
                BookingAttempt.objects.create(
                    **payload,
                    outcome=BookingAttempt.Outcome.REJECTED,
                    failure_code="BOOKING_SLOT_UNAVAILABLE",
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                payload = {
                    **valid_payload,
                    "booking": booking,
                    "client_request_id": uuid.uuid4(),
                }
                BookingAttempt.objects.create(
                    **payload,
                    outcome=BookingAttempt.Outcome.SUCCESS,
                    resolution=BookingAttempt.Resolution.DISMISSED,
                )

    def test_database_constraints_enforce_requested_source_and_recurring_intent(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_rejected_attempt(
                    self.court,
                    self.staff,
                    requested_source=Booking.Source.RECURRING,
                    requested_recurring=False,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_rejected_attempt(
                    self.court,
                    self.staff,
                    requested_source=Booking.Source.MANUAL,
                    requested_recurring=True,
                )

    def test_database_constraint_rejects_invalid_requested_interval(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_rejected_attempt(
                    self.court,
                    self.staff,
                    requested_start=self.time_at(21),
                    requested_end=self.time_at(20),
                )
