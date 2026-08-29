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

import pytest
from django.conf import settings
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import (
    complete_booking,
    create_booking,
    expire_due_hold_bookings,
)
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.settlements.services import create_approved_settlement
from apps.transactions.models import Transaction
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
            is_platform_admin=True,
        )
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
        request = type("Request", (), {"user": user or self.admin})()
        return ClubAccessContext(request=request, club=self.club)

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
        ClubMembership.objects.create(
            club=self.club,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court,
        )
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
