"""PostgreSQL row-lock concurrency tests.

SQLite does not prove select_for_update semantics. These tests skip unless
Django is configured with PostgreSQL.

Run:

    DB_ENGINE=postgresql pytest tests/test_postgresql_concurrency.py -q
"""

from __future__ import annotations

import threading
from datetime import time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import IntegrityError, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking, BookingAttempt
from apps.bookings.services import (
    cancel_booking,
    complete_booking,
    create_booking,
    expire_due_hold_bookings,
)
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.services import (
    find_or_create_player_profile,
    get_or_create_club_player,
)
from apps.profiles.models import AdminProfile, Profile, StaffProfile
from apps.settlements.services import create_approved_settlement
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.services import create_booking_transaction

pytestmark = pytest.mark.skipif(
    "postgresql" not in settings.DATABASES["default"]["ENGINE"],
    reason="PostgreSQL concurrency tests require DB_ENGINE=postgresql",
)


class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="pg-concurrency-admin",
            password="test-pass-123",
        )
        admin_profile = Profile.objects.create(user=self.admin, role=Profile.Role.ADMIN)
        AdminProfile.objects.create(profile=admin_profile)
        self.club = Club.objects.create(
            name="PG Concurrency Club",
            slug="pg-concurrency",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court = Court.objects.create(
            club=self.club,
            name="PG Court",
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
            internal_hold_expiry_hours=12,
        )
        for weekday in range(7):
            working_hour, _ = CourtWorkingHour.objects.update_or_create(
                court=self.court,
                weekday=weekday,
                defaults={},
            )
            working_hour.pricing_periods.all().delete()
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=working_hour,
                starts_at=time(8, 0),
                ends_at=time(23, 0),
                price=Decimal("300.00"),
            )

    def make_access(self, user=None):
        from rest_framework.test import APIRequestFactory

        from apps.common.authorization.resolver import resolve_club_scope

        target_user = user or self.admin
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club.slug}/")
        request.user = target_user
        return resolve_club_scope(request, self.club.slug)

    def slot(self, hour):
        now = timezone.localtime()
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if start <= now:
            start += timedelta(days=1)
        return start, start + timedelta(hours=1)

    def run_threads(self, workers):
        results = []
        errors = []

        def run(worker):
            close_old_connections()
            try:
                results.append(worker())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results, errors

    def test_overlapping_booking_create_allows_only_one_winner(self):
        start, end = self.slot(20)

        def worker():
            return create_booking(
                created_by=self.admin,
                court=self.court,
                start_time=start,
                end_time=end,
                customer_name="PG Customer",
                customer_phone="+201011111111",
            )

        results, errors = self.run_threads([worker, worker])
        created = [item for item in results if isinstance(item, Booking)]
        conflicts = [
            exc
            for exc in errors
            if isinstance(exc, SlotyAPIException)
            and exc.api_code == "BOOKING_SLOT_UNAVAILABLE"
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(conflicts), 1)

    def test_idempotent_booking_retry_race_creates_one_attempt_and_booking(self):
        start, end = self.slot(12)
        client_request_id = uuid4()

        def worker():
            return create_booking(
                created_by=self.admin,
                court=self.court,
                start_time=start,
                end_time=end,
                customer_name="Idempotent Booking Race",
                customer_phone="+201077777777",
                client_request_id=client_request_id,
            )

        results, errors = self.run_threads([worker, worker])
        booking_ids = {booking_obj.id for booking_obj in results}
        self.assertFalse(errors)
        self.assertEqual(booking_ids, {Booking.objects.get().id})
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingAttempt.objects.count(), 1)
        attempt = BookingAttempt.objects.get()
        self.assertEqual(attempt.outcome, BookingAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.booking_id, next(iter(booking_ids)))

    def test_payment_race_on_same_hold_confirms_once(self):
        start, end = self.slot(18)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Pay Race",
            customer_phone="+201022222222",
        )
        access = self.make_access()

        def worker(reference):
            return create_booking_transaction(
                access=access,
                booking=booking,
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference=reference,
                created_by=self.admin,
            )

        results, errors = self.run_threads(
            [
                lambda: worker("PG-PAY-1"),
                lambda: worker("PG-PAY-2"),
            ]
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(Transaction.objects.filter(booking=booking).count(), 2)

    def test_idempotent_transaction_retry_race_creates_one_attempt_and_transaction(
        self,
    ):
        start, end = self.slot(14)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Idempotent Pay Race",
            customer_phone="+201022222223",
        )
        access = self.make_access()
        client_request_id = uuid4()
        occurred_at = timezone.now() - timedelta(hours=2)

        def worker():
            return create_booking_transaction(
                access=access,
                booking=booking,
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference="PG-IDEMPOTENT-TX",
                client_request_id=client_request_id,
                occurred_at=occurred_at,
                occurred_at_provided=True,
                created_by=self.admin,
            )

        results, errors = self.run_threads([worker, worker])
        transaction_ids = {transaction_obj.id for transaction_obj in results}
        self.assertFalse(errors)
        self.assertEqual(transaction_ids, {Transaction.objects.get().id})
        self.assertEqual(Transaction.objects.filter(booking=booking).count(), 1)
        self.assertEqual(TransactionAttempt.objects.count(), 1)
        attempt = TransactionAttempt.objects.get()
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.transaction_id, next(iter(transaction_ids)))

    def test_payment_race_cannot_overpay_remaining_amount(self):
        start, end = self.slot(13)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Overpay Race",
            customer_phone="+201022222224",
        )
        booking.total_price = Decimal("75.00")
        booking.save(update_fields=["total_price"])
        access = self.make_access()

        def worker(reference):
            return create_booking_transaction(
                access=access,
                booking=booking,
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference=reference,
                created_by=self.admin,
            )

        results, errors = self.run_threads(
            [
                lambda: worker("PG-OVERPAY-1"),
                lambda: worker("PG-OVERPAY-2"),
            ]
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(errors)
        self.assertEqual(Transaction.objects.filter(booking=booking).count(), 1)

    def test_duplicate_payment_reference_race_rejects_second(self):
        start, end = self.slot(16)
        first = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Ref Race A",
            customer_phone="+201033333331",
        )
        second_start, second_end = self.slot(17)
        second = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=second_start,
            end_time=second_end,
            customer_name="Ref Race B",
            customer_phone="+201033333332",
        )
        access = self.make_access()

        def worker(booking):
            return create_booking_transaction(
                access=access,
                booking=booking,
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference="PG-SHARED-REF",
                created_by=self.admin,
            )

        results, errors = self.run_threads(
            [
                lambda: worker(first),
                lambda: worker(second),
            ]
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(errors)

    def test_settlement_race_settles_transactions_once(self):
        staff = User.objects.create_user(
            username="pg-settle-staff",
            password="test-pass-123",
        )
        staff_p = Profile.objects.create(user=staff, role=Profile.Role.STAFF)
        StaffProfile.objects.create(profile=staff_p, court=self.court)
        start, end = self.slot(15)
        booking = create_booking(
            created_by=staff,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Settle Race",
            customer_phone="+201044444444",
        )
        access = self.make_access()
        create_booking_transaction(
            access=access,
            booking=booking,
            amount=Decimal("50.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            payment_reference="PG-SETTLE",
            created_by=staff,
        )

        def worker():
            return create_approved_settlement(
                access=access,
                collected_by=staff,
                actor=self.admin,
            )

        results, errors = self.run_threads([worker, worker])
        successes = [item for item in results if item is not None]
        self.assertEqual(len(successes), 1)
        self.assertTrue(errors or len(successes) == 1)

    def test_hold_expiry_versus_payment_does_not_double_apply(self):
        start, end = self.slot(21)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Expiry Race",
            customer_phone="+201055555555",
        )
        Booking.objects.filter(pk=booking.pk).update(
            created=timezone.now() - timedelta(hours=13)
        )
        access = self.make_access()

        def pay():
            return create_booking_transaction(
                access=access,
                booking=booking,
                amount=Decimal("50.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference="PG-EXPIRY-PAY",
                created_by=self.admin,
            )

        def expire():
            return expire_due_hold_bookings()

        self.run_threads([pay, expire])
        booking.refresh_from_db()
        self.assertIn(
            booking.status,
            {Booking.Status.CONFIRMED, Booking.Status.EXPIRED},
        )

    def test_recurring_continuation_race_creates_one_next_booking(self):
        start, end = self.slot(19)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Recurrence Race",
            customer_phone="+201066666666",
            source=Booking.Source.RECURRING,
        )
        access = self.make_access()
        create_booking_transaction(
            access=access,
            booking=booking,
            amount=booking.total_price,
            payment_method=Transaction.PaymentMethod.CASH,
            payment_reference="PG-RECUR-PAY",
            created_by=self.admin,
        )

        def worker():
            return complete_booking(
                access=access,
                booking=booking,
                actor=self.admin,
                continue_recurring=True,
            )

        results, errors = self.run_threads([worker, worker])
        successes = [item for item in results if isinstance(item, Booking)]
        booking.refresh_from_db()
        next_count = Booking.objects.filter(previous_recurring_booking=booking).count()
        self.assertEqual(len(successes), 1)
        self.assertEqual(next_count, 1)
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(
            booking.recurrence_status,
            Booking.RecurrenceStatus.RENEWED,
        )
        self.assertTrue(errors)

    def test_player_profile_concurrent_same_phone_creates_single_record(self):
        phone = "+201099990099"

        def worker():
            profile, _ = find_or_create_player_profile(
                phone_number=phone,
                full_name="Concurrent Player",
            )
            return profile

        results, errors = self.run_threads([worker, worker])
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual(PlayerProfile.objects.filter(phone_number=phone).count(), 1)

    def test_club_player_concurrent_resolution_creates_single_current_version(self):
        profile, _ = find_or_create_player_profile(
            phone_number="+201099990088",
            full_name="Concurrent CP Profile",
        )

        def worker():
            club_player, _ = get_or_create_club_player(
                club=self.club,
                player_profile=profile,
                display_name="Concurrent CP",
            )
            return club_player

        results, errors = self.run_threads([worker, worker])
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=self.club,
                player_profile=profile,
                is_current_version=True,
            ).count(),
            1,
        )

    def test_booking_state_transition_race_cancel_versus_complete(self):
        start, end = self.slot(10)
        booking = create_booking(
            created_by=self.admin,
            court=self.court,
            start_time=start,
            end_time=end,
            customer_name="Transition Race",
            customer_phone="+201099990077",
        )
        access = self.make_access()
        create_booking_transaction(
            access=access,
            booking=booking,
            amount=booking.total_price,
            payment_method=Transaction.PaymentMethod.CASH,
            payment_reference="PG-TRANS-PAY",
            created_by=self.admin,
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        def do_cancel():
            return cancel_booking(
                access=access,
                booking=booking,
                actor=self.admin,
                reason="Race Cancel",
            )

        def do_complete():
            return complete_booking(
                access=access,
                booking=booking,
                actor=self.admin,
            )

        results, errors = self.run_threads([do_cancel, do_complete])
        booking.refresh_from_db()
        self.assertIn(
            booking.status,
            {Booking.Status.CANCELLED, Booking.Status.COMPLETED},
        )
        # Exactly one action must succeed and one must fail
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)

    def test_staff_profile_uniqueness_race_enforces_single_profile(self):
        staff_user = User.objects.create_user(
            username="pg-staff-race-user",
            password="test-pass-123",
        )
        profile = Profile.objects.create(user=staff_user, role=Profile.Role.STAFF)

        def worker():
            return StaffProfile.objects.create(
                profile=profile,
                court=self.court,
            )

        results, errors = self.run_threads([worker, worker])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        from django.core.exceptions import ValidationError

        self.assertTrue(isinstance(errors[0], (IntegrityError, ValidationError)))
        self.assertEqual(
            StaffProfile.objects.filter(profile=profile).count(),
            1,
        )
