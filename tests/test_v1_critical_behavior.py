from datetime import time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.transactions.models import Transaction
from apps.transactions.services import get_booking_paid_amount
from tests.booking_factories import persist_booking
from tests.profile_factories import ProfileIdentityMixin, grant_owner_club


class V1CriticalBehaviorTestCase(ProfileIdentityMixin, APITestCase):
    """
    Automated regression suite for V1 critical business behavior.

    Focuses on:
    1. Booking core flows (cancellation releases slot via API)
    2. Financial integrity (model fields across the transaction lifecycle)
    3. Critical validation (phone, missing fields, amounts, invalid IDs)
    4. Terminal status safeguards (completed bookings stay locked)
    5. Tenant and permission isolation on mutations
    """

    password = "test-pass-123"

    def setUp(self):
        self.owner = self.create_user()
        self.club = self.create_club("Al Ahly Club", slug="al-ahly")
        grant_owner_club(self.owner, self.club)

        self.court = self.create_court(
            self.club, "Main Football Court", default_price=Decimal("300.00")
        )
        # Setup working hours for all 7 days so any future date works
        for day in range(7):
            CourtWorkingHour.objects.filter(court=self.court, weekday=day).delete()
            wh = CourtWorkingHour.objects.create(court=self.court, weekday=day)
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=wh,
                starts_at=time(8, 0),
                ends_at=time(23, 0),
                price=Decimal("300.00"),
            )
        self.court.minimum_deposit = Decimal("100.00")
        self.court.save(update_fields=["minimum_deposit"])

        # Other club for tenant isolation checks
        self.other_owner = self.create_user()
        self.other_club = self.create_club("Zamalek Club", slug="zamalek")
        grant_owner_club(self.other_owner, self.other_club)
        self.other_court = self.create_court(
            self.other_club, "Zamalek Court 1", default_price=Decimal("250.00")
        )

        self.client.force_authenticate(user=self.owner)

    def create_club(self, name: str, slug: str) -> Club:
        return Club.objects.create(
            name=name,
            slug=slug,
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_court(
        self, club: Club, name: str, default_price: Decimal = Decimal("300.00")
    ) -> Court:
        return Court.objects.create(
            club=club,
            name=name,
            default_price=default_price,
            minimum_deposit=Decimal("100.00"),
            slot_duration_minutes=60,
            cancellation_refund_notice_days=0,
        )

    def future_time_at(self, days_ahead: int, hour: int, minute: int = 0):
        base = timezone.now() + timedelta(days=days_ahead)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # --- Booking URLs ---
    def booking_list_url(self, club):
        return reverse("club-booking-list", kwargs={"club_slug": club.slug})

    def booking_detail_url(self, club, booking):
        return reverse(
            "club-booking-detail", kwargs={"club_slug": club.slug, "pk": booking.pk}
        )

    def booking_lifecycle_url(self, club, booking, action_name):
        return reverse(
            f"club-booking-{action_name}",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    # --- Transaction URLs ---
    def transaction_list_url(self, club):
        return reverse("club-transaction-list", kwargs={"club_slug": club.slug})

    def transaction_cancel_url(self, club, transaction_obj):
        return reverse(
            "club-transaction-cancel",
            kwargs={"club_slug": club.slug, "pk": transaction_obj.pk},
        )

    def assert_field_error(self, response, field):
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("success"), False)
        self.assertIn(field, response.data.get("field_errors", {}))

    # =========================================================================
    # 1. Booking Core Flow: Cancellation Releases Slot via API
    # =========================================================================
    def test_booking_cancellation_releases_slot_for_new_booking(self):
        """
        Prove end-to-end:
        1. Create booking in slot (18:00 - 19:00 tomorrow) -> 201 Created.
        2. Attempting another booking in the same slot is rejected with 409 Conflict.
        3. Cancel the active booking with a valid reason -> 200 OK.
        4. Creating a new booking in that exact same slot now succeeds with 201 Created.
        """
        start = self.future_time_at(2, 18)
        end = self.future_time_at(2, 19)

        payload = {
            "court": self.court.id,
            "customer_name": "Customer One",
            "customer_phone": "+201011112222",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }
        create_res1 = self.client.post(
            self.booking_list_url(self.club), payload, format="json"
        )
        self.assertEqual(create_res1.status_code, status.HTTP_201_CREATED)
        booking1_id = create_res1.data["id"]

        # 2. Conflicting booking attempt
        payload2 = {
            "court": self.court.id,
            "customer_name": "Customer Two",
            "customer_phone": "+201033334444",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }
        conflict_res = self.client.post(
            self.booking_list_url(self.club), payload2, format="json"
        )
        self.assertEqual(conflict_res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflict_res.data["code"], "BOOKING_SLOT_UNAVAILABLE")

        # 3. Cancel first booking
        booking1 = Booking.objects.get(id=booking1_id)
        cancel_url = self.booking_lifecycle_url(self.club, booking1, "cancel")
        cancel_res = self.client.post(
            cancel_url, {"reason": "Customer cancelled via phone call"}, format="json"
        )
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)
        booking1.refresh_from_db()
        self.assertEqual(booking1.status, Booking.Status.CANCELLED)

        # 4. Now slot is released: Customer Two can book it
        create_res2 = self.client.post(
            self.booking_list_url(self.club), payload2, format="json"
        )
        self.assertEqual(create_res2.status_code, status.HTTP_201_CREATED)
        booking2 = Booking.objects.get(id=create_res2.data["id"])
        self.assertEqual(booking2.status, Booking.Status.HOLD)

    # =========================================================================
    # 2. Financial Integrity: Multi-Step Lifecycle & Model Assertions
    # =========================================================================
    def test_financial_integrity_lifecycle_model_assertions(self):
        """
        Verify that at every step of the payment and cancellation lifecycle,
        the model's stored and calculated financial attributes are strictly correct:
          - booking.total_price
          - booking.paid_amount
          - booking.remaining_amount
          - booking.status
        """
        start = self.future_time_at(3, 19)
        end = self.future_time_at(3, 20)

        # Step 1: Create booking with total_price = 300.00
        booking = persist_booking(
            self.court,
            start_time=start,
            end_time=end,
            total_price=Decimal("300.00"),
            status=Booking.Status.HOLD,
        )

        booking.refresh_from_db()
        paid = get_booking_paid_amount(booking)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        self.assertEqual(paid, Decimal("0.00"))
        self.assertEqual(booking.total_price - paid, Decimal("300.00"))
        self.assertEqual(booking.status, Booking.Status.HOLD)

        # Step 2: Pay minimum deposit (100.00) -> booking transitions to CONFIRMED
        tx_payload1 = {
            "booking": booking.id,
            "amount": "100.00",
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        tx_res1 = self.client.post(
            self.transaction_list_url(self.club), tx_payload1, format="json"
        )
        self.assertEqual(tx_res1.status_code, status.HTTP_201_CREATED)
        tx1_id = tx_res1.data["id"]

        booking.refresh_from_db()
        paid = get_booking_paid_amount(booking)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        self.assertEqual(paid, Decimal("100.00"))
        self.assertEqual(booking.total_price - paid, Decimal("200.00"))
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        # Step 3: Pay remaining amount (200.00) -> booking is fully paid
        tx_payload2 = {
            "booking": booking.id,
            "amount": "200.00",
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        tx_res2 = self.client.post(
            self.transaction_list_url(self.club), tx_payload2, format="json"
        )
        self.assertEqual(tx_res2.status_code, status.HTTP_201_CREATED)
        tx2_id = tx_res2.data["id"]

        booking.refresh_from_db()
        paid = get_booking_paid_amount(booking)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        self.assertEqual(paid, Decimal("300.00"))
        self.assertEqual(booking.total_price - paid, Decimal("0.00"))
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        # Step 4: Cancel second payment (200.00) -> still CONFIRMED
        tx2 = Transaction.objects.get(id=tx2_id)
        cancel_tx2_res = self.client.post(
            self.transaction_cancel_url(self.club, tx2),
            {"reason": "Customer entered duplicate payment by mistake"},
            format="json",
        )
        self.assertEqual(cancel_tx2_res.status_code, status.HTTP_200_OK)

        booking.refresh_from_db()
        paid = get_booking_paid_amount(booking)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        self.assertEqual(paid, Decimal("100.00"))
        self.assertEqual(booking.total_price - paid, Decimal("200.00"))
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

        # Step 5: Cancel first payment (deposit 100.00) -> HOLD, balance 0
        tx1 = Transaction.objects.get(id=tx1_id)
        cancel_tx1_res = self.client.post(
            self.transaction_cancel_url(self.club, tx1),
            {"reason": "Reversing deposit payment"},
            format="json",
        )
        self.assertEqual(cancel_tx1_res.status_code, status.HTTP_200_OK)

        booking.refresh_from_db()
        paid = get_booking_paid_amount(booking)
        self.assertEqual(booking.total_price, Decimal("300.00"))
        self.assertEqual(paid, Decimal("0.00"))
        self.assertEqual(booking.total_price - paid, Decimal("300.00"))
        self.assertEqual(booking.status, Booking.Status.HOLD)

    # =========================================================================
    # 3. Validation: Booking Creation (Missing Fields, Invalid Phone, Wrong Club Court)
    # =========================================================================
    def test_booking_validation_missing_fields_and_invalid_phone(self):
        """Important validation rules on booking creation."""
        url = self.booking_list_url(self.club)
        start = self.future_time_at(4, 10)
        end = self.future_time_at(4, 11)

        # Missing court
        res_no_court = self.client.post(
            url,
            {
                "customer_name": "Test Customer",
                "customer_phone": "+201000000001",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
            format="json",
        )
        self.assert_field_error(res_no_court, "court")

        # Missing start_time
        res_no_start = self.client.post(
            url,
            {
                "court": self.court.id,
                "customer_name": "Test Customer",
                "customer_phone": "+201000000001",
                "end_time": end.isoformat(),
            },
            format="json",
        )
        self.assert_field_error(res_no_start, "start_time")

        # Invalid phone format
        res_invalid_phone = self.client.post(
            url,
            {
                "court": self.court.id,
                "customer_name": "Test Customer",
                "customer_phone": "invalid-phone-format",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
            format="json",
        )
        self.assert_field_error(res_invalid_phone, "customer_phone")

        # Court ID belonging to another club
        res_other_court = self.client.post(
            url,
            {
                "court": self.other_court.id,
                "customer_name": "Test Customer",
                "customer_phone": "+201000000001",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
            },
            format="json",
        )
        self.assertIn(
            res_other_court.status_code,
            [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND],
        )

    # =========================================================================
    # 4. Validation: Financial Values (Zero, Negative, Exceeding Balance)
    # =========================================================================
    def test_transaction_validation_zero_negative_and_excess_amounts(self):
        """Reject invalid payment amounts: zero, negative, or over remaining."""
        start = self.future_time_at(5, 12)
        end = self.future_time_at(5, 13)

        booking = persist_booking(
            self.court,
            start_time=start,
            end_time=end,
            total_price=Decimal("300.00"),
            status=Booking.Status.HOLD,
        )
        url = self.transaction_list_url(self.club)

        # Zero amount
        res_zero = self.client.post(
            url,
            {
                "booking": booking.id,
                "amount": "0.00",
                "payment_method": Transaction.PaymentMethod.CASH,
            },
            format="json",
        )
        self.assert_field_error(res_zero, "amount")

        # Negative amount
        res_neg = self.client.post(
            url,
            {
                "booking": booking.id,
                "amount": "-50.00",
                "payment_method": Transaction.PaymentMethod.CASH,
            },
            format="json",
        )
        self.assert_field_error(res_neg, "amount")

        # Amount exceeding remaining balance (> 300.00)
        res_excess = self.client.post(
            url,
            {
                "booking": booking.id,
                "amount": "350.00",
                "payment_method": Transaction.PaymentMethod.CASH,
            },
            format="json",
        )
        self.assert_field_error(res_excess, "amount")

    # =========================================================================
    # 5. Terminal Booking Safeguards
    # =========================================================================
    def test_terminal_booking_cannot_be_modified_or_paid(self):
        """Completed booking cannot be rescheduled, paid, or cancelled."""
        start = self.future_time_at(6, 14)
        end = self.future_time_at(6, 15)

        booking = persist_booking(
            self.court,
            start_time=start,
            end_time=end,
            total_price=Decimal("300.00"),
            status=Booking.Status.COMPLETED,
        )

        # 1. Reschedule attempt
        reschedule_url = self.booking_lifecycle_url(self.club, booking, "reschedule")
        res_reschedule = self.client.post(
            reschedule_url,
            {
                "start_time": self.future_time_at(6, 16).isoformat(),
                "end_time": self.future_time_at(6, 17).isoformat(),
            },
            format="json",
        )
        self.assertIn(
            res_reschedule.status_code,
            [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT],
        )

        # 2. Payment attempt
        payment_url = self.transaction_list_url(self.club)
        res_payment = self.client.post(
            payment_url,
            {
                "booking": booking.id,
                "amount": "50.00",
                "payment_method": Transaction.PaymentMethod.CASH,
            },
            format="json",
        )
        self.assertIn(
            res_payment.status_code,
            [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT],
        )

        # 3. Cancel attempt
        cancel_url = self.booking_lifecycle_url(self.club, booking, "cancel")
        res_cancel = self.client.post(
            cancel_url,
            {"reason": "Attempt to cancel completed booking"},
            format="json",
        )
        self.assertIn(
            res_cancel.status_code,
            [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT],
        )

    # =========================================================================
    # 6. Tenant and Role Isolation on Mutations
    # =========================================================================
    def test_cross_club_isolation_on_booking_and_payment(self):
        """Owner of Club B cannot create booking or record payment in Club A."""
        self.client.force_authenticate(user=self.other_owner)
        start = self.future_time_at(7, 10)
        end = self.future_time_at(7, 11)

        # Attempt to create booking in Club A
        payload = {
            "court": self.court.id,
            "customer_name": "Outsider Customer",
            "customer_phone": "+201099998888",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }
        res_create = self.client.post(
            self.booking_list_url(self.club), payload, format="json"
        )
        self.assertIn(
            res_create.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
        )

        # Existing Club A booking
        booking = persist_booking(
            self.court,
            start_time=start,
            end_time=end,
            status=Booking.Status.HOLD,
        )

        # Attempt to post transaction in Club A
        tx_payload = {
            "booking": booking.id,
            "amount": "50.00",
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        res_tx = self.client.post(
            self.transaction_list_url(self.club), tx_payload, format="json"
        )
        self.assertIn(
            res_tx.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND]
        )
