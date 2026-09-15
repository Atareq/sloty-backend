from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.profiles.models import Profile, StaffProfile
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.services import get_booking_paid_amount
from tests.booking_factories import persist_booking


class TransactionAttemptModelTests(TestCase):
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
        extra_fields.setdefault("customer_name", "Attempt Payment Customer")
        extra_fields.setdefault("customer_phone", "+201000000001")
        return persist_booking(court, **extra_fields)

    def create_transaction(self, booking: Booking, **extra_fields) -> Transaction:
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
            "created_by": self.staff,
        }
        data.update(extra_fields)
        return Transaction.objects.create(**data)

    def create_rejected_attempt(self, booking: Booking, **extra_fields):
        data = {
            "booking": booking,
            "club": booking.club,
            "court": booking.court,
            "attempted_by": self.staff,
            "client_request_id": uuid4(),
            "amount": Decimal("500.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
            "payment_reference": "ATTEMPT-REF",
            "notes": "offline payment note",
            "occurred_at": self.time_at(19),
            "outcome": TransactionAttempt.Outcome.REJECTED,
            "failure_code": "PAYMENT_AMOUNT_EXCEEDS_REMAINING",
            "failure_details": {"amount": ["Too much"]},
        }
        data.update(extra_fields)
        return TransactionAttempt.objects.create(**data)

    def setUp(self):
        self.club = self.create_club("Payment Attempt Club", "payment-attempt")
        self.other_club = self.create_club("Other Attempt Club", "other-pay-attempt")
        self.court = self.create_court(self.club, "Payment Attempt Court")
        self.other_court = self.create_court(self.other_club, "Other Court")
        self.staff = self.create_user("payment-attempt-staff")
        self.create_user("other-payment-staff")
        profile = Profile.objects.create(user=self.staff, role=Profile.Role.STAFF)
        self.staff_profile = StaffProfile.objects.create(
            profile=profile, court=self.court
        )
        self.booking = self.create_booking(self.court)
        self.other_booking = self.create_booking(self.other_court)

    def test_rejected_attempt_preserves_original_payment_intent(self):
        client_request_id = uuid4()
        attempt = self.create_rejected_attempt(
            self.booking,
            client_request_id=client_request_id,
            amount=Decimal("275.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            payment_reference="WALLET-123",
            notes="Customer paid while offline",
            occurred_at=self.time_at(18),
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.club, self.club)
        self.assertEqual(attempt.court, self.court)
        self.assertEqual(attempt.booking, self.booking)
        self.assertEqual(attempt.attempted_by, self.staff)
        self.assertEqual(attempt.client_request_id, client_request_id)
        self.assertEqual(attempt.amount, Decimal("275.00"))
        self.assertEqual(
            attempt.payment_method,
            Transaction.PaymentMethod.DIGITAL_WALLET,
        )
        self.assertEqual(attempt.payment_reference, "WALLET-123")
        self.assertEqual(attempt.notes, "Customer paid while offline")
        self.assertEqual(attempt.occurred_at, self.time_at(18))
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.failure_code, "PAYMENT_AMOUNT_EXCEEDS_REMAINING")
        self.assertEqual(attempt.failure_details, {"amount": ["Too much"]})
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.UNRESOLVED)
        self.assertIsNotNone(attempt.created)
        self.assertIsNotNone(attempt.modified)
        self.assertEqual(self.staff_profile.court.club, attempt.club)
        self.assertEqual(self.staff_profile.court, attempt.court)
        self.assertEqual(self.staff_profile.profile.user, attempt.attempted_by)

    def test_successful_attempt_can_reference_resulting_transaction(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("125.00"),
            payment_reference="ORIGINAL-REF",
            notes="Original note",
        )
        attempt = TransactionAttempt.objects.create(
            club=self.club,
            court=self.court,
            booking=self.booking,
            attempted_by=self.staff,
            transaction=transaction_obj,
            client_request_id=uuid4(),
            amount=transaction_obj.amount,
            payment_method=transaction_obj.payment_method,
            payment_reference=transaction_obj.payment_reference,
            notes=transaction_obj.notes,
            occurred_at=transaction_obj.occurred_at,
            outcome=TransactionAttempt.Outcome.SUCCESS,
            resolution=TransactionAttempt.Resolution.RESOLVED,
        )

        self.assertEqual(attempt.transaction, transaction_obj)
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.failure_code, "")
        self.assertIn(attempt, transaction_obj.attempts.all())

    def test_rejected_attempt_does_not_create_fake_transaction_or_financial_rows(self):
        self.assertEqual(Transaction.objects.count(), 0)

        attempt = self.create_rejected_attempt(self.booking)

        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.REJECTED)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("0.00"))
        self.assertEqual(Settlement.objects.count(), 0)

    def test_attempt_preserves_original_values_when_transaction_changes_later(self):
        transaction_obj = self.create_transaction(self.booking)
        attempt = TransactionAttempt.objects.create(
            club=self.club,
            court=self.court,
            booking=self.booking,
            attempted_by=self.staff,
            transaction=transaction_obj,
            client_request_id=uuid4(),
            amount=Decimal("125.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            payment_reference="ORIGINAL-REF",
            notes="Original note",
            occurred_at=self.time_at(18),
            outcome=TransactionAttempt.Outcome.SUCCESS,
            resolution=TransactionAttempt.Resolution.RESOLVED,
        )

        transaction_obj.is_cancelled = True
        transaction_obj.cancelled_at = timezone.now()
        transaction_obj.cancellation_reason = "Wrong amount"
        transaction_obj.payment_reference = "CHANGED-REF"
        transaction_obj.save(
            update_fields=[
                "is_cancelled",
                "cancelled_at",
                "cancellation_reason",
                "payment_reference",
            ]
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.amount, Decimal("125.00"))
        self.assertEqual(attempt.payment_reference, "ORIGINAL-REF")
        self.assertEqual(attempt.notes, "Original note")
        self.assertEqual(attempt.occurred_at, self.time_at(18))
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)

    def test_full_clean_rejects_cross_club_booking_and_transaction_mismatches(self):
        other_transaction = self.create_transaction(self.other_booking)

        with self.assertRaises(ValidationError) as booking_error:
            TransactionAttempt(
                club=self.club,
                court=self.court,
                booking=self.other_booking,
                attempted_by=self.staff,
                client_request_id=uuid4(),
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                occurred_at=self.time_at(19),
                outcome=TransactionAttempt.Outcome.REJECTED,
                failure_code="PAYMENT_AMOUNT_EXCEEDS_REMAINING",
            ).full_clean()
        self.assertIn("club", booking_error.exception.message_dict)

        with self.assertRaises(ValidationError) as transaction_error:
            TransactionAttempt(
                club=self.club,
                court=self.court,
                booking=self.booking,
                attempted_by=self.staff,
                transaction=other_transaction,
                client_request_id=uuid4(),
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                occurred_at=self.time_at(19),
                outcome=TransactionAttempt.Outcome.SUCCESS,
            ).full_clean()
        self.assertIn("transaction", transaction_error.exception.message_dict)

    def test_client_request_id_is_unique_per_club_for_attempts(self):
        client_request_id = uuid4()
        self.create_rejected_attempt(
            self.booking,
            client_request_id=client_request_id,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_rejected_attempt(
                    self.booking,
                    client_request_id=client_request_id,
                )

        other_attempt = self.create_rejected_attempt(
            self.other_booking,
            client_request_id=client_request_id,
        )
        self.assertEqual(other_attempt.club, self.other_club)

    def test_attempts_without_client_request_id_are_allowed(self):
        first = self.create_rejected_attempt(self.booking, client_request_id=None)
        second = self.create_rejected_attempt(
            self.booking,
            client_request_id=None,
            occurred_at=self.time_at(18),
        )

        self.assertIsNone(first.client_request_id)
        self.assertIsNone(second.client_request_id)
        self.assertEqual(TransactionAttempt.objects.count(), 2)

    def test_database_constraints_enforce_outcome_amount_and_resolution(self):
        transaction_obj = self.create_transaction(self.booking)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TransactionAttempt.objects.create(
                    club=self.club,
                    court=self.court,
                    booking=self.booking,
                    attempted_by=self.staff,
                    client_request_id=uuid4(),
                    amount=Decimal("50.00"),
                    payment_method=Transaction.PaymentMethod.CASH,
                    occurred_at=self.time_at(19),
                    outcome=TransactionAttempt.Outcome.SUCCESS,
                    resolution=TransactionAttempt.Resolution.DISMISSED,
                    transaction=transaction_obj,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TransactionAttempt.objects.create(
                    club=self.club,
                    court=self.court,
                    booking=self.booking,
                    attempted_by=self.staff,
                    client_request_id=uuid4(),
                    amount=Decimal("0.00"),
                    payment_method=Transaction.PaymentMethod.CASH,
                    occurred_at=self.time_at(19),
                    outcome=TransactionAttempt.Outcome.REJECTED,
                    failure_code="PAYMENT_AMOUNT_EXCEEDS_REMAINING",
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TransactionAttempt.objects.create(
                    club=self.club,
                    court=self.court,
                    booking=self.booking,
                    attempted_by=self.staff,
                    client_request_id=uuid4(),
                    amount=Decimal("50.00"),
                    payment_method=Transaction.PaymentMethod.CASH,
                    occurred_at=self.time_at(19),
                    outcome=TransactionAttempt.Outcome.REJECTED,
                )
