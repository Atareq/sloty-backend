from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import ClubMembership
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.services import get_booking_paid_amount
from tests.transactions.test_transaction_api import TransactionAPITestCase


class TransactionCancelAPITests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("cancel-admin")
        self.owner = self.create_user("cancel-owner")
        self.manager = self.create_user("cancel-manager")
        self.staff = self.create_user("cancel-staff")
        self.other_user = self.create_user("cancel-other-user")
        self.club = self.create_club("Cancel Club", slug="cancel-club")
        self.other_club = self.create_club(
            "Other Cancel Club", slug="other-cancel-club"
        )
        self.court = self.create_court(self.club, "Cancel Court")
        self.other_court = self.create_court(self.club, "Other Cancel Court")
        self.external_court = self.create_court(self.other_club, "External Court")
        self.booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
        )
        self.other_court_booking = self.create_booking(
            self.other_court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(21),
            end_time=self.time_at(22),
        )
        self.external_booking = self.create_booking(
            self.external_court,
            status=Booking.Status.CONFIRMED,
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
            self.other_user,
            self.other_club,
            ClubMembership.Role.OWNER,
        )

    def cancel_url(self, club, transaction_obj):
        return reverse(
            "club-transaction-cancel",
            kwargs={"club_slug": club.slug, "pk": transaction_obj.pk},
        )

    def post_cancel(self, club, transaction_obj, actor, payload=None):
        self.client.force_authenticate(user=actor)
        return self.client.post(
            self.cancel_url(club, transaction_obj),
            {"reason": "Wrong amount entered"} if payload is None else payload,
            format="json",
        )

    def test_transaction_cancel_fields_default_to_empty_history_state(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        self.assertFalse(transaction_obj.is_cancelled)
        self.assertIsNone(transaction_obj.cancelled_by)
        self.assertIsNone(transaction_obj.cancelled_at)
        self.assertEqual(transaction_obj.cancellation_reason, "")

    def test_unauthenticated_user_cannot_cancel(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        response = self.client.post(
            self.cancel_url(self.club, transaction_obj),
            {"reason": "Wrong amount"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_without_selected_club_access_cannot_cancel(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        response = self.post_cancel(self.club, transaction_obj, self.other_user)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_manager_and_staff_can_cancel_own_eligible_transaction(self):
        actors = (self.owner, self.manager, self.staff)
        for index, actor in enumerate(actors):
            with self.subTest(actor=actor.username):
                booking = self.create_booking(
                    self.court,
                    status=Booking.Status.CONFIRMED,
                    customer_phone=f"+20100000010{index}",
                    start_time=self.time_at(10 + index),
                    end_time=self.time_at(11 + index),
                )
                transaction_obj = self.create_transaction(booking, created_by=actor)

                response = self.post_cancel(self.club, transaction_obj, actor)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                transaction_obj.refresh_from_db()
                self.assertTrue(transaction_obj.is_cancelled)

    def test_non_platform_users_cannot_cancel_another_users_transaction(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.other_user,
        )

        for actor in (self.owner, self.manager, self.staff):
            with self.subTest(actor=actor.username):
                response = self.post_cancel(self.club, transaction_obj, actor)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_cancel_transaction_from_another_court(self):
        transaction_obj = self.create_transaction(
            self.other_court_booking,
            created_by=self.staff,
        )

        response = self.post_cancel(self.club, transaction_obj, self.staff)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_platform_admin_can_cancel_any_eligible_transaction(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        response = self.post_cancel(self.club, transaction_obj, self.platform_admin)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_reason_is_required_and_blank_reason_is_rejected(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        missing_response = self.post_cancel(
            self.club,
            transaction_obj,
            self.owner,
            {},
        )
        blank_response = self.post_cancel(
            self.club,
            transaction_obj,
            self.owner,
            {"reason": "   "},
        )

        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(blank_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(missing_response, "reason")
        self.assert_field_error(blank_response, "reason")

    def test_already_cancelled_transaction_cannot_be_cancelled_again(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
            is_cancelled=True,
            cancelled_by=self.owner,
            cancelled_at=timezone.now(),
            cancellation_reason="First correction",
        )

        response = self.post_cancel(self.club, transaction_obj, self.owner)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "PAYMENT_ALREADY_CANCELLED")
        self.assertNotIn("transaction", response.data)

    def test_settled_transaction_cannot_be_cancelled(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )
        settlement = Settlement.objects.create(
            club=self.club,
            court=self.court,
            period_start=self.time_at(1),
            period_end=self.time_at(2),
            total_amount=transaction_obj.amount,
            transaction_count=1,
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=transaction_obj,
            amount=transaction_obj.amount,
        )

        response = self.post_cancel(self.club, transaction_obj, self.owner)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "PAYMENT_SETTLED_CANNOT_BE_CANCELLED")
        self.assertNotIn("transaction", response.data)
        transaction_obj.refresh_from_db()
        self.assertFalse(transaction_obj.is_cancelled)

    def test_transactions_on_terminal_bookings_cannot_be_cancelled(self):
        for index, booking_status in enumerate(Booking.LOCKED_STATUSES):
            with self.subTest(booking_status=booking_status):
                booking = self.create_booking(
                    self.court,
                    status=booking_status,
                    customer_phone=f"+20100000020{index}",
                    start_time=self.time_at(14 + index),
                    end_time=self.time_at(15 + index),
                )
                transaction_obj = self.create_transaction(
                    booking,
                    created_by=self.owner,
                )

                response = self.post_cancel(self.club, transaction_obj, self.owner)

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assert_api_error(response, "PAYMENT_BOOKING_LOCKED")
                self.assertNotIn("booking", response.data)
                transaction_obj.refresh_from_db()
                self.assertFalse(transaction_obj.is_cancelled)

    def test_cancel_sets_metadata_returns_detail_and_creates_audit_logs(self):
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        response = self.post_cancel(
            self.club,
            transaction_obj,
            self.owner,
            {"reason": "  Wrong amount entered  "},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_cancelled"])
        self.assertEqual(response.data["cancelled_by"], self.owner.id)
        self.assertIsNotNone(response.data["cancelled_at"])
        self.assertEqual(response.data["cancellation_reason"], "Wrong amount entered")
        transaction_obj.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HOLD)
        cancel_log = AuditLog.objects.get(
            action=AuditLog.Action.TRANSACTION_CANCELLED,
            entity_type="Transaction",
            entity_id=transaction_obj.id,
        )
        self.assertEqual(cancel_log.before_data["is_cancelled"], False)
        self.assertEqual(cancel_log.after_data["is_cancelled"], True)
        self.assertEqual(cancel_log.metadata["reason"], "Wrong amount entered")
        self.assertEqual(cancel_log.metadata["booking_id"], self.booking.id)
        booking_log = AuditLog.objects.get(
            action=AuditLog.Action.BOOKING_UPDATED,
            entity_type="Booking",
            entity_id=self.booking.id,
            metadata__source="transaction_cancel",
        )
        self.assertEqual(booking_log.before_data["status"], Booking.Status.CONFIRMED)
        self.assertEqual(booking_log.after_data["status"], Booking.Status.HOLD)

    def test_confirmed_booking_stays_confirmed_when_valid_payment_remains(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("100.00"),
            created_by=self.owner,
        )
        self.create_transaction(
            self.booking,
            amount=Decimal("50.00"),
            created_by=self.manager,
        )

        response = self.post_cancel(self.club, transaction_obj, self.owner)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("50.00"))

    def test_cancel_on_hold_booking_keeps_hold_status(self):
        self.booking.status = Booking.Status.HOLD
        self.booking.save(update_fields=["status"])
        transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )

        response = self.post_cancel(self.club, transaction_obj, self.owner)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HOLD)

    def test_cancel_updates_payment_summary_and_corrected_create_uses_valid_total(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("250.00"),
            created_by=self.owner,
        )
        self.post_cancel(self.club, transaction_obj, self.owner)
        self.client.force_authenticate(user=self.owner)

        detail_response = self.client.get(
            reverse(
                "club-booking-detail",
                kwargs={"club_slug": self.club.slug, "pk": self.booking.pk},
            )
        )
        create_response = self.post_transaction(
            self.club,
            self.booking,
            amount="300.00",
            payment_reference="CORRECTED-001",
        )

        self.assertEqual(detail_response.data["paid_amount"], "0.00")
        self.assertEqual(detail_response.data["remaining_amount"], "300.00")
        self.assertFalse(detail_response.data["is_fully_paid"])
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("300.00"))

    def test_list_includes_cancelled_history_and_is_cancelled_filter_works(self):
        valid_transaction = self.create_transaction(
            self.booking,
            created_by=self.owner,
        )
        cancelled_transaction = self.create_transaction(
            self.booking,
            created_by=self.owner,
            is_cancelled=True,
            cancelled_by=self.owner,
            cancelled_at=timezone.now(),
            cancellation_reason="Historical correction",
        )
        self.client.force_authenticate(user=self.owner)

        all_response = self.client.get(self.transaction_list_url(self.club))
        cancelled_response = self.client.get(
            self.transaction_list_url(self.club),
            {"is_cancelled": "true"},
        )
        valid_response = self.client.get(
            self.transaction_list_url(self.club),
            {"is_cancelled": "false"},
        )

        self.assertEqual(
            self.list_ids(all_response),
            {valid_transaction.id, cancelled_transaction.id},
        )
        self.assertEqual(self.list_ids(cancelled_response), {cancelled_transaction.id})
        self.assertEqual(self.list_ids(valid_response), {valid_transaction.id})
