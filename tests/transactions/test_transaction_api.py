from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import yaml
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import (
    get_current_unsettled_transactions,
    summarize_current_custody_transactions,
)
from apps.transactions.filters import TransactionFilter
from apps.transactions.models import Transaction, TransactionAttempt
from apps.transactions.services import (
    DUPLICATE_PAYMENT_REFERENCE_MESSAGE,
    create_booking_transaction,
    get_booking_paid_amount,
)
from apps.transactions.views import TransactionViewSet


class TransactionAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="transaction-admin") -> User:
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
        }
        data.update(extra_fields)
        return Court.objects.create(**data)

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

    def time_at(self, hour: int, minute: int = 0):
        return timezone.datetime(
            2026,
            5,
            20,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
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

    def transaction_list_url(self, club):
        return reverse("club-transaction-list", kwargs={"club_slug": club.slug})

    def transaction_detail_url(self, club, transaction_obj):
        return reverse(
            "club-transaction-detail",
            kwargs={"club_slug": club.slug, "pk": transaction_obj.pk},
        )

    def transaction_attempt_list_url(self, club):
        return reverse("club-transaction-attempt-list", kwargs={"club_slug": club.slug})

    def transaction_attempt_detail_url(self, club, attempt):
        return reverse(
            "club-transaction-attempt-detail",
            kwargs={"club_slug": club.slug, "pk": attempt.pk},
        )

    def transaction_attempt_dismiss_url(self, club, attempt):
        return reverse(
            "club-transaction-attempt-dismiss",
            kwargs={"club_slug": club.slug, "pk": attempt.pk},
        )

    def transaction_payload(self, booking: Booking, **extra_fields):
        data = {
            "booking": booking.id,
            "amount": "50.00",
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        return data

    def post_transaction(self, club: Club, booking: Booking, **extra_fields):
        return self.client.post(
            self.transaction_list_url(club),
            self.transaction_payload(booking, **extra_fields),
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

    def make_access(self, user, club):
        request = type("Request", (), {"user": user})()
        return ClubAccessContext(request=request, club=club)


class TransactionModelServiceTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.club = self.create_club("Model Club", slug="model-club")
        self.other_club = self.create_club("Other Model Club", slug="other-model")
        self.court = self.create_court(self.club, "Model Court")
        self.other_court = self.create_court(self.other_club, "Other Model Court")
        self.booking = self.create_booking(self.court)
        self.other_booking = self.create_booking(self.other_court)

    def test_can_create_transaction_with_required_fields(self):
        transaction_obj = self.create_transaction(self.booking)

        self.assertEqual(transaction_obj.booking, self.booking)
        self.assertEqual(transaction_obj.amount, Decimal("50.00"))
        self.assertEqual(transaction_obj.payment_method, Transaction.PaymentMethod.CASH)

    def test_transaction_copies_club_and_court_from_booking(self):
        transaction_obj = self.create_transaction(self.booking)

        self.assertEqual(transaction_obj.club, self.booking.club)
        self.assertEqual(transaction_obj.court, self.booking.court)

    def test_transaction_amount_must_be_positive(self):
        transaction_obj = Transaction(
            booking=self.booking,
            amount=Decimal("0.00"),
            payment_method=Transaction.PaymentMethod.CASH,
        )

        with self.assertRaises(DjangoValidationError):
            transaction_obj.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_transaction(self.booking, amount=Decimal("0.00"))

    def test_duplicate_non_blank_payment_reference_in_same_club_is_rejected(self):
        self.create_transaction(self.booking, payment_reference="REF-1")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_transaction(self.booking, payment_reference="REF-1")

        access = self.make_access(self.platform_admin, self.club)
        with self.assertRaises(DRFValidationError) as exc:
            create_booking_transaction(
                access=access,
                booking=self.booking,
                amount=Decimal("25.00"),
                payment_method=Transaction.PaymentMethod.CASH,
                payment_reference="REF-1",
                created_by=self.platform_admin,
            )
        self.assertEqual(
            exc.exception.detail["payment_reference"][0],
            DUPLICATE_PAYMENT_REFERENCE_MESSAGE,
        )

    def test_same_non_blank_payment_reference_in_different_clubs_is_allowed(self):
        first = self.create_transaction(self.booking, payment_reference="SHARED")
        second = self.create_transaction(self.other_booking, payment_reference="SHARED")

        self.assertEqual(first.payment_reference, second.payment_reference)
        self.assertNotEqual(first.club, second.club)

    def test_blank_payment_reference_can_repeat(self):
        first = self.create_transaction(self.booking, payment_reference="")
        second = self.create_transaction(self.booking, payment_reference="")

        self.assertEqual(first.payment_reference, "")
        self.assertEqual(second.payment_reference, "")

    def test_payment_reference_is_trimmed_before_saving(self):
        transaction_obj = self.create_transaction(
            self.booking,
            payment_reference="  TRIMMED-REF  ",
        )

        self.assertEqual(transaction_obj.payment_reference, "TRIMMED-REF")


class TransactionAccessTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("access-admin")
        self.owner = self.create_user("access-owner")
        self.manager = self.create_user("access-manager")
        self.staff = self.create_user("access-staff")
        self.other_user = self.create_user("access-other")
        self.club = self.create_club("Access Club", slug="access-club")
        self.other_club = self.create_club("Other Access Club", slug="other-access")
        self.court = self.create_court(self.club, "Access Court")
        self.same_club_other_court = self.create_court(self.club, "Other Court")
        self.other_court = self.create_court(self.other_club, "External Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(self.same_club_other_court)
        self.other_booking = self.create_booking(self.other_court)
        self.transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.staff,
        )
        self.same_court_other_collector_transaction = self.create_transaction(
            self.booking,
            created_by=self.manager,
            payment_reference="MGR-SAME-COURT",
        )
        self.same_club_other_transaction = self.create_transaction(
            self.same_club_other_booking,
            created_by=self.manager,
            payment_reference="MGR-OTHER-COURT",
        )
        self.other_transaction = self.create_transaction(self.other_booking)
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

    def test_anonymous_cannot_access_transactions(self):
        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_list_transactions_in_selected_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {
                self.transaction_obj.id,
                self.same_court_other_collector_transaction.id,
                self.same_club_other_transaction.id,
            },
        )
        self.assertNotIn(self.other_transaction.id, self.list_ids(response))

    def test_owner_can_list_transactions_in_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {
                self.transaction_obj.id,
                self.same_court_other_collector_transaction.id,
                self.same_club_other_transaction.id,
            },
        )

    def test_manager_can_list_transactions_in_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {
                self.transaction_obj.id,
                self.same_court_other_collector_transaction.id,
                self.same_club_other_transaction.id,
            },
        )

    def test_staff_can_list_transactions_for_assigned_court_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})
        self.assertNotIn(self.same_club_other_transaction.id, self.list_ids(response))
        self.assertNotIn(
            self.same_court_other_collector_transaction.id,
            self.list_ids(response),
        )

    def test_staff_cannot_broaden_transaction_list_with_created_by(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"created_by": self.manager.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), set())

    def test_staff_cannot_retrieve_another_collector_on_assigned_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_detail_url(
                self.club,
                self.same_court_other_collector_transaction,
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_cannot_retrieve_transaction_for_another_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_detail_url(self.club, self.same_club_other_transaction)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_with_other_club_membership_cannot_access_selected_club(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TransactionResponseContextTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("response-admin")
        self.club = self.create_club("Response Club", slug="response-club")
        self.court = self.create_court(self.club, "Response Court")
        self.booking = self.create_booking(self.court)
        self.transaction_obj = self.create_transaction(
            self.booking,
            created_by=self.platform_admin,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def assert_context_fields(self, payload, transaction_obj):
        self.assertEqual(
            payload["booking_start_time"],
            self.booking.start_time.isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(
            payload["booking_end_time"],
            self.booking.end_time.isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(payload["court_name"], self.court.name)
        self.assertEqual(payload["created_by_username"], self.platform_admin.username)
        self.assertEqual(payload["booking"], self.booking.id)
        self.assertEqual(payload["court"], self.court.id)
        self.assertEqual(payload["created_by"], self.platform_admin.id)
        self.assertEqual(payload["id"], transaction_obj.id)
        self.assertEqual(payload["booking_customer_name"], self.booking.customer_name)
        self.assertEqual(payload["booking_customer_phone"], self.booking.customer_phone)

    def test_list_response_includes_booking_court_and_creator_context(self):
        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_context_fields(response.data["results"][0], self.transaction_obj)

    def test_detail_response_includes_booking_court_and_creator_context(self):
        response = self.client.get(
            self.transaction_detail_url(self.club, self.transaction_obj)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_context_fields(response.data, self.transaction_obj)

    def test_created_by_username_is_empty_when_created_by_is_null(self):
        self.transaction_obj.created_by = None
        self.transaction_obj.save(update_fields=["created_by"])

        response = self.client.get(
            self.transaction_detail_url(self.club, self.transaction_obj)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["created_by"])
        self.assertEqual(response.data["created_by_username"], "")


class TransactionClubPlayerIdentityTests(TransactionAPITestCase):
    def test_transaction_display_keeps_booking_club_player_version(self):
        from datetime import time

        from apps.bookings.services import create_booking
        from apps.courts.models import CourtWorkingHour, CourtWorkingHourPricePeriod
        from apps.players.services import create_club_player_version

        admin = self.create_platform_admin("identity-tx-admin")
        club = self.create_club("Identity Tx Club", slug="identity-tx-club")
        court = self.create_court(club, "Identity Tx Court")
        working_hour = CourtWorkingHour.objects.create(court=court, weekday=2)
        CourtWorkingHourPricePeriod.objects.create(
            working_hour=working_hour,
            starts_at=time(9, 0),
            ends_at=time(23, 0),
            price=court.default_price,
        )
        booking = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            customer_name="Ahmed Ali",
            customer_phone="+201088880020",
        )
        create_club_player_version(
            booking.club_player, display_name="Ahmed Salah", player_number=10
        )
        transaction_obj = self.create_transaction(booking, created_by=admin)
        self.client.force_authenticate(user=admin)

        response = self.client.get(self.transaction_detail_url(club, transaction_obj))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["booking_customer_name"], "Ahmed Ali")
        self.assertEqual(response.data["booking_customer_phone"], "+201088880020")


class TransactionCreateTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("create-admin")
        self.owner = self.create_user("create-owner")
        self.manager = self.create_user("create-manager")
        self.staff = self.create_user("create-staff")
        self.club = self.create_club("Create Club", slug="create-transaction")
        self.other_club = self.create_club("Other Create Club", slug="other-create-tx")
        self.court = self.create_court(self.club, "Create Court")
        self.same_club_other_court = self.create_court(self.club, "Create Other Court")
        self.other_court = self.create_court(self.other_club, "External Create Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(self.same_club_other_court)
        self.other_booking = self.create_booking(self.other_court)
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_platform_admin_can_create_transaction_for_booking_in_selected_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction_obj = Transaction.objects.get(id=response.data["id"])
        self.assertEqual(transaction_obj.created_by, self.platform_admin)

    def test_owner_can_create_transaction_for_booking_in_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_transaction(self.club, self.booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_manager_can_create_transaction_for_booking_in_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_transaction(self.club, self.booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_can_create_transaction_for_booking_on_assigned_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(self.club, self.booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_cannot_create_transaction_for_booking_on_another_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(self.club, self.same_club_other_booking)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_create_transaction_for_booking_from_another_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.other_booking)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "TRANSACTION_BOOKING_NOT_IN_CLUB")
        self.assertNotIn("booking", response.data)

    def test_cannot_create_transaction_for_inaccessible_booking(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(self.club, self.same_club_other_booking)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_create_transaction_with_non_positive_amount(self):
        self.client.force_authenticate(user=self.platform_admin)

        zero_response = self.post_transaction(self.club, self.booking, amount="0.00")
        negative_response = self.post_transaction(
            self.club,
            self.booking,
            amount="-1.00",
        )

        self.assertEqual(zero_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(negative_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_transaction_that_overpays_booking(self):
        self.create_transaction(self.booking, amount=Decimal("275.00"))
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="50.00")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "amount")

    def test_can_create_partial_payment(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="100.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["amount"], "100.00")

    def test_first_payment_must_meet_court_minimum_deposit(self):
        self.court.minimum_deposit = Decimal("75.00")
        self.court.save(update_fields=["minimum_deposit"])
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="50.00")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "amount")
        self.assertEqual(self.booking.transactions.count(), 0)

    def test_can_create_second_payment_up_to_remaining_amount(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="200.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_historical_offline_payment_preserves_event_time_and_server_created(self):
        occurred_at = self.time_at(9) - timedelta(days=3)
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            occurred_at=occurred_at.isoformat(),
            payment_reference="OFFLINE-HISTORICAL-1",
            amount="75.00",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction_obj = Transaction.objects.get(id=response.data["id"])
        self.assertEqual(transaction_obj.created_by, self.staff)
        self.assertEqual(transaction_obj.occurred_at, occurred_at)
        self.assertEqual(
            str(transaction_obj.client_request_id),
            response.data["client_request_id"],
        )
        self.assertNotEqual(transaction_obj.created, occurred_at)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)

    def test_transaction_occurred_at_requires_timezone_aware_value(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            occurred_at="2026-05-18T09:00:00",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "occurred_at")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_idempotent_retry_returns_existing_transaction_without_duplicate_audit(
        self,
    ):
        client_request_id = str(uuid4())
        occurred_at = self.time_at(9) - timedelta(days=1)
        payload = {
            "client_request_id": client_request_id,
            "occurred_at": occurred_at.isoformat(),
            "amount": "100.00",
            "payment_reference": "IDEMPOTENT-TX-1",
            "notes": "offline retry",
        }
        self.client.force_authenticate(user=self.staff)

        first_response = self.post_transaction(self.club, self.booking, **payload)
        retry_response = self.post_transaction(self.club, self.booking, **payload)

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["id"], retry_response.data["id"])
        self.assertEqual(
            Transaction.objects.filter(client_request_id=client_request_id).count(),
            1,
        )
        self.assertEqual(
            TransactionAttempt.objects.filter(client_request_id=client_request_id)
            .filter(outcome=TransactionAttempt.Outcome.SUCCESS)
            .count(),
            1,
        )
        self.assertEqual(self.booking.transactions.count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.TRANSACTION_CREATED,
                entity_type="Transaction",
                entity_id=first_response.data["id"],
            ).count(),
            1,
        )

    def test_idempotency_conflict_rejects_different_logical_transaction(self):
        client_request_id = str(uuid4())
        self.client.force_authenticate(user=self.platform_admin)

        first_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            amount="50.00",
            payment_reference="IDEMPOTENT-CONFLICT-1",
        )
        mismatch_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            amount="60.00",
            payment_reference="IDEMPOTENT-CONFLICT-1",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mismatch_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(
            mismatch_response,
            "TRANSACTION_CLIENT_REQUEST_MISMATCH",
        )
        self.assertEqual(
            Transaction.objects.filter(client_request_id=client_request_id).count(),
            1,
        )
        self.assertEqual(
            TransactionAttempt.objects.filter(client_request_id=client_request_id)
            .filter(outcome=TransactionAttempt.Outcome.SUCCESS)
            .count(),
            1,
        )

    def test_idempotency_conflict_includes_different_occurred_at(self):
        client_request_id = str(uuid4())
        occurred_at = self.time_at(9) - timedelta(days=1)
        self.client.force_authenticate(user=self.platform_admin)

        first_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            occurred_at=occurred_at.isoformat(),
            payment_reference="IDEMPOTENT-CONFLICT-TIME",
        )
        mismatch_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            occurred_at=(occurred_at + timedelta(minutes=5)).isoformat(),
            payment_reference="IDEMPOTENT-CONFLICT-TIME",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mismatch_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(
            mismatch_response,
            "TRANSACTION_CLIENT_REQUEST_MISMATCH",
        )

    def test_historical_payment_uses_existing_current_custody_semantics(self):
        occurred_at = self.time_at(7) - timedelta(days=10)
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            occurred_at=occurred_at.isoformat(),
            amount="125.00",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        access = self.make_access(self.staff, self.club)
        custody_transactions = list(
            get_current_unsettled_transactions(access=access, collected_by=self.staff)
        )
        summary = summarize_current_custody_transactions(custody_transactions)
        transaction_obj = Transaction.objects.get(id=response.data["id"])
        self.assertEqual(custody_transactions, [transaction_obj])
        self.assertEqual(summary["net_amount"], Decimal("125.00"))
        self.assertEqual(summary["period_start"], transaction_obj.created)
        self.assertNotEqual(summary["period_start"], transaction_obj.occurred_at)

    def test_revoked_staff_access_cannot_sync_transaction(self):
        membership = ClubMembership.objects.get(user=self.staff, club=self.club)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            occurred_at=(self.time_at(8) - timedelta(days=1)).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(TransactionAttempt.objects.count(), 0)


class TransactionAttemptTraceabilityAPITests(TransactionAPITestCase):
    def setUp(self):
        self.owner = self.create_user("tx-attempt-owner")
        self.manager = self.create_user("tx-attempt-manager")
        self.staff = self.create_user("tx-attempt-staff")
        self.other_staff = self.create_user("tx-attempt-other-staff")
        self.external_user = self.create_user("tx-attempt-external")
        self.club = self.create_club("Transaction Attempt Club", slug="tx-attempt")
        self.other_club = self.create_club("Other Tx Attempt", slug="other-tx-attempt")
        self.court = self.create_court(self.club, "Transaction Attempt Court")
        self.other_court = self.create_court(self.club, "Other Attempt Court")
        self.external_court = self.create_court(self.other_club, "External Court")
        self.booking = self.create_booking(self.court)
        self.other_booking = self.create_booking(
            self.other_court,
            start_time=self.time_at(21),
            end_time=self.time_at(22),
        )
        self.external_booking = self.create_booking(self.external_court)
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

    def create_attempt(self, booking, attempted_by, **extra_fields):
        data = {
            "club": booking.club,
            "court": booking.court,
            "booking": booking,
            "attempted_by": attempted_by,
            "client_request_id": uuid4(),
            "amount": Decimal("500.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
            "payment_reference": "ATTEMPT-REF",
            "notes": "original payment attempt",
            "occurred_at": self.time_at(19),
            "outcome": TransactionAttempt.Outcome.REJECTED,
            "failure_code": "PAYMENT_AMOUNT_EXCEEDS_REMAINING",
            "failure_details": {"amount": ["Too much"]},
        }
        data.update(extra_fields)
        return TransactionAttempt.objects.create(**data)

    def attempt_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def test_accepted_payment_records_attempt_and_resolved_transaction(self):
        client_request_id = str(uuid4())
        occurred_at = self.time_at(18) - timedelta(days=2)
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            occurred_at=occurred_at.isoformat(),
            amount="100.00",
            payment_reference="ACCEPTED-ATTEMPT",
            notes="offline payment",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("attempt", response.data)
        transaction_obj = Transaction.objects.get(pk=response.data["id"])
        attempt = TransactionAttempt.objects.get(client_request_id=client_request_id)
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.RESOLVED)
        self.assertEqual(attempt.transaction, transaction_obj)
        self.assertEqual(attempt.booking, self.booking)
        self.assertEqual(attempt.attempted_by, self.staff)
        self.assertEqual(attempt.amount, Decimal("100.00"))
        self.assertEqual(attempt.payment_reference, "ACCEPTED-ATTEMPT")
        self.assertEqual(attempt.notes, "offline payment")
        self.assertEqual(attempt.occurred_at, occurred_at)
        self.assertEqual(attempt.failure_code, "")

    def test_accepted_attempt_updates_payment_custody_and_settlement_candidates(self):
        self.client.force_authenticate(user=self.staff)
        create_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            amount="100.00",
            payment_reference="ACCEPTED-FINANCIAL-STATE",
        )
        transaction_obj = Transaction.objects.get(pk=create_response.data["id"])
        access = self.make_access(self.owner, self.club)

        custody_transactions = list(
            get_current_unsettled_transactions(access=access, collected_by=self.staff)
        )
        custody = summarize_current_custody_transactions(custody_transactions)

        self.client.force_authenticate(user=self.owner)
        preview_response = self.client.get(
            reverse("club-settlement-preview", kwargs={"club_slug": self.club.slug}),
            {"collected_by": self.staff.id},
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("100.00"))
        self.assertEqual(custody_transactions, [transaction_obj])
        self.assertEqual(custody["net_amount"], Decimal("100.00"))
        self.assertEqual(preview_response.status_code, status.HTTP_200_OK)
        self.assertEqual(preview_response.data["transaction_count"], 1)
        self.assertEqual(preview_response.data["total_amount"], "100.00")
        self.assertEqual(TransactionAttempt.objects.get().transaction, transaction_obj)

    def test_rejected_payment_records_attempt_without_fake_transaction(self):
        self.create_transaction(
            self.booking,
            amount=Decimal("275.00"),
            created_by=self.staff,
        )
        client_request_id = str(uuid4())
        occurred_at = self.time_at(18) - timedelta(days=1)
        self.client.force_authenticate(user=self.staff)

        response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            occurred_at=occurred_at.isoformat(),
            amount="50.00",
            payment_reference="REJECTED-ATTEMPT",
            notes="too much",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "amount")
        self.assertEqual(Transaction.objects.count(), 1)
        attempt = TransactionAttempt.objects.get(client_request_id=client_request_id)
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.REJECTED)
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.UNRESOLVED)
        self.assertEqual(attempt.transaction, None)
        self.assertEqual(attempt.failure_code, "PAYMENT_AMOUNT_EXCEEDS_REMAINING")
        self.assertEqual(attempt.amount, Decimal("50.00"))
        self.assertEqual(attempt.payment_reference, "REJECTED-ATTEMPT")
        self.assertEqual(attempt.occurred_at, occurred_at)

    def test_rejected_attempt_retry_does_not_duplicate_attempt_or_transaction(self):
        self.create_transaction(
            self.booking,
            amount=Decimal("275.00"),
            created_by=self.staff,
        )
        client_request_id = str(uuid4())
        payload = {
            "client_request_id": client_request_id,
            "amount": "50.00",
            "payment_reference": "RETRY-REJECTED",
            "notes": "same rejected retry",
        }
        self.client.force_authenticate(user=self.staff)

        first_response = self.post_transaction(self.club, self.booking, **payload)
        retry_response = self.post_transaction(self.club, self.booking, **payload)

        self.assertEqual(first_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(retry_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(retry_response, "amount")
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionAttempt.objects.count(), 1)
        self.assertEqual(
            TransactionAttempt.objects.get().failure_code,
            "PAYMENT_AMOUNT_EXCEEDS_REMAINING",
        )

    def test_rejected_attempt_client_request_mismatch_does_not_overwrite(self):
        self.create_transaction(
            self.booking,
            amount=Decimal("275.00"),
            created_by=self.staff,
        )
        client_request_id = str(uuid4())
        self.client.force_authenticate(user=self.staff)

        first_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            amount="50.00",
            payment_reference="REJECTED-MISMATCH",
        )
        mismatch_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=client_request_id,
            amount="60.00",
            payment_reference="REJECTED-MISMATCH",
        )

        self.assertEqual(first_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(mismatch_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(
            mismatch_response,
            "TRANSACTION_CLIENT_REQUEST_MISMATCH",
        )
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionAttempt.objects.count(), 1)
        self.assertEqual(TransactionAttempt.objects.get().amount, Decimal("50.00"))

    def test_owner_and_manager_can_list_club_attempts(self):
        own_attempt = self.create_attempt(self.booking, self.staff)
        other_attempt = self.create_attempt(
            self.booking,
            self.other_staff,
            occurred_at=self.time_at(18),
        )
        external_attempt = self.create_attempt(
            self.external_booking,
            self.external_user,
        )

        for actor in (self.owner, self.manager):
            with self.subTest(actor=actor.username):
                self.client.force_authenticate(user=actor)
                response = self.client.get(self.transaction_attempt_list_url(self.club))

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    self.attempt_ids(response),
                    {own_attempt.id, other_attempt.id},
                )
                self.assertNotIn(external_attempt.id, self.attempt_ids(response))

    def test_staff_lists_only_own_attempts_in_assigned_scope(self):
        own_attempt = self.create_attempt(self.booking, self.staff)
        other_staff_attempt = self.create_attempt(
            self.booking,
            self.other_staff,
            occurred_at=self.time_at(18),
        )
        other_court_attempt = self.create_attempt(
            self.other_booking,
            self.staff,
            occurred_at=self.time_at(17),
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.transaction_attempt_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.attempt_ids(response), {own_attempt.id})
        self.assertNotIn(other_staff_attempt.id, self.attempt_ids(response))
        self.assertNotIn(other_court_attempt.id, self.attempt_ids(response))

    def test_attempt_filters_by_status_court_employee_method_and_paid_date(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("75.00"),
            created_by=self.staff,
            payment_reference="FILTER-ACCEPTED-TX",
        )
        accepted_attempt = self.create_attempt(
            self.booking,
            self.staff,
            transaction=transaction_obj,
            amount=Decimal("75.00"),
            payment_reference="FILTER-ACCEPTED-TX",
            outcome=TransactionAttempt.Outcome.SUCCESS,
            failure_code="",
            failure_details={},
            resolution=TransactionAttempt.Resolution.RESOLVED,
        )
        dismissed_attempt = self.create_attempt(
            self.booking,
            self.staff,
            payment_method=Transaction.PaymentMethod.BANK_TRANSFER,
            resolution=TransactionAttempt.Resolution.DISMISSED,
        )
        self.create_attempt(
            self.other_booking,
            self.other_staff,
            occurred_at=self.time_at(22),
        )
        self.client.force_authenticate(user=self.owner)

        accepted_response = self.client.get(
            self.transaction_attempt_list_url(self.club),
            {"status": "ACCEPTED"},
        )
        dismissed_response = self.client.get(
            self.transaction_attempt_list_url(self.club),
            {"status": "DISMISSED"},
        )
        scoped_response = self.client.get(
            self.transaction_attempt_list_url(self.club),
            {
                "court": self.court.id,
                "attempted_by": self.staff.id,
                "payment_method": Transaction.PaymentMethod.BANK_TRANSFER,
                "date": "2026-05-20",
            },
        )

        self.assertEqual(self.attempt_ids(accepted_response), {accepted_attempt.id})
        self.assertEqual(self.attempt_ids(dismissed_response), {dismissed_attempt.id})
        self.assertEqual(self.attempt_ids(scoped_response), {dismissed_attempt.id})

    def test_attempt_detail_exposes_original_request_and_backend_decision(self):
        attempt = self.create_attempt(
            self.booking,
            self.staff,
            amount=Decimal("275.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            payment_reference="DETAIL-REF",
            notes="original detail",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.transaction_attempt_detail_url(self.club, attempt)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "REJECTED")
        self.assertEqual(response.data["outcome"], TransactionAttempt.Outcome.REJECTED)
        self.assertEqual(
            response.data["resolution"],
            TransactionAttempt.Resolution.UNRESOLVED,
        )
        self.assertEqual(
            response.data["failure_code"],
            "PAYMENT_AMOUNT_EXCEEDS_REMAINING",
        )
        self.assertEqual(response.data["failure_details"], {"amount": ["Too much"]})
        self.assertEqual(response.data["amount"], "275.00")
        self.assertEqual(
            response.data["payment_method"],
            Transaction.PaymentMethod.DIGITAL_WALLET,
        )
        self.assertEqual(response.data["payment_reference"], "DETAIL-REF")
        self.assertEqual(response.data["notes"], "original detail")
        self.assertIsNone(response.data["resolved_transaction"])

    def test_attempt_original_request_cannot_be_patched_or_put(self):
        attempt = self.create_attempt(
            self.booking,
            self.staff,
            amount=Decimal("275.00"),
            notes="immutable payment attempt",
        )
        self.client.force_authenticate(user=self.owner)

        patch_response = self.client.patch(
            self.transaction_attempt_detail_url(self.club, attempt),
            {"amount": "75.00", "notes": "edited"},
            format="json",
        )
        put_response = self.client.put(
            self.transaction_attempt_detail_url(self.club, attempt),
            {"amount": "75.00", "notes": "edited"},
            format="json",
        )

        self.assertEqual(patch_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(put_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        attempt.refresh_from_db()
        self.assertEqual(attempt.amount, Decimal("275.00"))
        self.assertEqual(attempt.notes, "immutable payment attempt")

    def test_staff_can_dismiss_own_rejected_attempt_without_fake_transaction(self):
        attempt = self.create_attempt(self.booking, self.staff)
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "DISMISSED")
        attempt.refresh_from_db()
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.DISMISSED)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("0.00"))
        self.assertEqual(Settlement.objects.count(), 0)

    def test_accepted_attempt_cannot_be_dismissed(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("75.00"),
            created_by=self.staff,
        )
        attempt = self.create_attempt(
            self.booking,
            self.staff,
            transaction=transaction_obj,
            amount=Decimal("75.00"),
            outcome=TransactionAttempt.Outcome.SUCCESS,
            failure_code="",
            failure_details={},
            resolution=TransactionAttempt.Resolution.RESOLVED,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "TRANSACTION_ATTEMPT_CANNOT_BE_DISMISSED")
        attempt.refresh_from_db()
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.RESOLVED)
        self.assertTrue(Transaction.objects.filter(pk=transaction_obj.pk).exists())

    def test_staff_cannot_view_or_dismiss_another_staff_attempt(self):
        attempt = self.create_attempt(self.booking, self.other_staff)
        self.client.force_authenticate(user=self.staff)

        detail_response = self.client.get(
            self.transaction_attempt_detail_url(self.club, attempt)
        )
        dismiss_response = self.client.post(
            self.transaction_attempt_dismiss_url(self.club, attempt),
            {},
            format="json",
        )

        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(dismiss_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_accepted_attempt_remains_accepted_after_transaction_cancel(self):
        self.client.force_authenticate(user=self.staff)
        create_response = self.post_transaction(
            self.club,
            self.booking,
            client_request_id=str(uuid4()),
            amount="100.00",
            payment_reference="CANCEL-LATER",
            notes="original payment",
        )
        transaction_obj = Transaction.objects.get(pk=create_response.data["id"])
        attempt = TransactionAttempt.objects.get(transaction=transaction_obj)

        cancel_response = self.client.post(
            reverse(
                "club-transaction-cancel",
                kwargs={"club_slug": self.club.slug, "pk": transaction_obj.pk},
            ),
            {"reason": "Wrong payment entered"},
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(cancel_response.status_code, status.HTTP_200_OK)
        attempt.refresh_from_db()
        self.assertEqual(attempt.outcome, TransactionAttempt.Outcome.SUCCESS)
        self.assertEqual(attempt.resolution, TransactionAttempt.Resolution.RESOLVED)
        self.assertEqual(attempt.transaction, transaction_obj)
        self.assertEqual(attempt.amount, Decimal("100.00"))
        self.assertEqual(attempt.payment_reference, "CANCEL-LATER")
        self.assertEqual(attempt.notes, "original payment")

    def test_rejected_attempts_have_zero_custody_settlement_and_payment_effect(self):
        real_payment = self.create_transaction(
            self.booking,
            amount=Decimal("300.00"),
            created_by=self.staff,
            payment_reference="REAL-CUSTODY",
        )
        rejected_attempt = self.create_attempt(
            self.booking,
            self.staff,
            amount=Decimal("500.00"),
            payment_reference="REJECTED-CUSTODY",
        )
        access = self.make_access(self.owner, self.club)
        self.client.force_authenticate(user=self.owner)

        custody_transactions = list(
            get_current_unsettled_transactions(access=access, collected_by=self.staff)
        )
        custody = summarize_current_custody_transactions(custody_transactions)
        preview_response = self.client.get(
            reverse("club-settlement-preview", kwargs={"club_slug": self.club.slug}),
            {"collected_by": self.staff.id},
        )
        settlement_response = self.client.post(
            reverse("club-settlement-list", kwargs={"club_slug": self.club.slug}),
            {"collected_by": self.staff.id},
            format="json",
        )

        self.assertEqual(TransactionAttempt.objects.count(), 1)
        self.assertEqual(rejected_attempt.transaction, None)
        self.assertEqual(custody_transactions, [real_payment])
        self.assertEqual(custody["net_amount"], Decimal("300.00"))
        self.assertEqual(get_booking_paid_amount(self.booking), Decimal("300.00"))
        self.assertEqual(preview_response.status_code, status.HTTP_200_OK)
        self.assertEqual(preview_response.data["transaction_count"], 1)
        self.assertEqual(preview_response.data["total_amount"], "300.00")
        self.assertEqual(settlement_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(settlement_response.data["transaction_count"], 1)
        self.assertEqual(SettlementTransaction.objects.count(), 1)
        self.assertEqual(SettlementTransaction.objects.get().transaction, real_payment)

    def test_rejected_attempts_do_not_appear_in_transaction_list(self):
        attempt = self.create_attempt(self.booking, self.staff)
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(self.list_ids(response), set())
        self.assertTrue(TransactionAttempt.objects.filter(pk=attempt.pk).exists())

    def test_membership_soft_delete_preserves_attempt_transaction_and_user(self):
        transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("75.00"),
            created_by=self.staff,
            payment_reference="DELETE-MEMBERSHIP-TX",
        )
        attempt = self.create_attempt(
            self.booking,
            self.staff,
            transaction=transaction_obj,
            amount=Decimal("75.00"),
            payment_reference="DELETE-MEMBERSHIP-TX",
            outcome=TransactionAttempt.Outcome.SUCCESS,
            failure_code="",
            failure_details={},
            resolution=TransactionAttempt.Resolution.RESOLVED,
        )
        settlement = Settlement.objects.create(
            club=self.club,
            court=self.court,
            collected_by=self.staff,
            period_start=self.time_at(8),
            period_end=self.time_at(14),
            status=Settlement.Status.SETTLED,
            total_amount=transaction_obj.amount,
            transaction_count=1,
            created_by=self.owner,
            settled_by=self.owner,
            settled_at=self.time_at(14),
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=transaction_obj,
            amount=transaction_obj.amount,
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
        self.assertEqual(attempt.transaction, transaction_obj)
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=transaction_obj.pk).exists())


class TransactionBookingConfirmationTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("confirm-admin")
        self.club = self.create_club("Confirm Club", slug="confirm-club")
        self.court = self.create_court(self.club, "Confirm Court")
        self.client.force_authenticate(user=self.platform_admin)

    def assert_status_rejects_transaction(self, booking_status):
        booking = self.create_booking(self.court, status=booking_status)

        response = self.post_transaction(self.club, booking)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "TRANSACTION_BOOKING_LOCKED")
        self.assertNotIn("booking", response.data)

    def test_first_valid_transaction_for_hold_booking_confirms_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_transaction(self.club, booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_transaction_for_confirmed_booking_keeps_status_confirmed(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_transaction(self.club, booking)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_transaction_cannot_be_created_for_completed_booking(self):
        self.assert_status_rejects_transaction(Booking.Status.COMPLETED)

    def test_transaction_cannot_be_created_for_cancelled_booking(self):
        self.assert_status_rejects_transaction(Booking.Status.CANCELLED)

    def test_transaction_cannot_be_created_for_no_show_booking(self):
        self.assert_status_rejects_transaction(Booking.Status.NO_SHOW)

    def test_transaction_cannot_be_created_for_expired_booking(self):
        self.assert_status_rejects_transaction(Booking.Status.EXPIRED)


class TransactionPaymentReferenceTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("reference-admin")
        self.club = self.create_club("Reference Club", slug="reference-club")
        self.other_club = self.create_club("Other Reference Club", slug="other-ref")
        self.court = self.create_court(
            self.club,
            "Reference Court",
            requires_digital_payment_reference=True,
        )
        self.other_court = self.create_court(
            self.other_club,
            "Other Reference Court",
            requires_digital_payment_reference=True,
        )
        self.booking = self.create_booking(self.court)
        self.other_booking = self.create_booking(self.other_court)
        self.client.force_authenticate(user=self.platform_admin)

    def test_digital_wallet_requires_payment_reference_when_court_requires_it(self):
        response = self.post_transaction(
            self.club,
            self.booking,
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            payment_reference="",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "payment_reference")

    def test_bank_transfer_requires_payment_reference_when_court_requires_it(self):
        response = self.post_transaction(
            self.club,
            self.booking,
            payment_method=Transaction.PaymentMethod.BANK_TRANSFER,
            payment_reference="",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "payment_reference")

    def test_cash_does_not_require_payment_reference(self):
        response = self.post_transaction(
            self.club,
            self.booking,
            payment_method=Transaction.PaymentMethod.CASH,
            payment_reference="",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_duplicate_non_blank_payment_reference_in_same_club_is_rejected(self):
        self.create_transaction(self.booking, payment_reference="DUPLICATE")

        response = self.post_transaction(
            self.club,
            self.booking,
            payment_reference="DUPLICATE",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["field_errors"]["payment_reference"][0]["message"],
            DUPLICATE_PAYMENT_REFERENCE_MESSAGE,
        )

    def test_same_non_blank_payment_reference_in_different_clubs_is_allowed(self):
        self.create_transaction(self.other_booking, payment_reference="CROSS-CLUB")

        response = self.post_transaction(
            self.club,
            self.booking,
            payment_reference="CROSS-CLUB",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_blank_payment_reference_is_allowed_for_cash(self):
        response = self.post_transaction(
            self.club,
            self.booking,
            payment_reference="",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class TransactionImmutabilityTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("immutable-admin")
        self.club = self.create_club("Immutable Club", slug="immutable-club")
        self.court = self.create_court(self.club, "Immutable Court")
        self.booking = self.create_booking(self.court)
        self.transaction_obj = self.create_transaction(self.booking)
        self.client.force_authenticate(user=self.platform_admin)

    def test_patch_put_and_delete_are_not_allowed(self):
        detail_url = self.transaction_detail_url(self.club, self.transaction_obj)

        patch_response = self.client.patch(
            detail_url, {"amount": "1.00"}, format="json"
        )
        put_response = self.client.put(detail_url, {"amount": "1.00"}, format="json")
        delete_response = self.client.delete(detail_url)

        self.assertEqual(patch_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(put_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(
            delete_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )
        self.transaction_obj.refresh_from_db()
        self.assertEqual(self.transaction_obj.amount, Decimal("50.00"))


class TransactionFilterTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("filter-admin")
        self.staff = self.create_user("filter-staff")
        self.club = self.create_club("Filter Club", slug="transaction-filter")
        self.other_club = self.create_club("Other Filter Club", slug="other-filter-tx")
        self.court = self.create_court(self.club, "Filter Court")
        self.same_club_other_court = self.create_court(self.club, "Filter Other Court")
        self.other_court = self.create_court(self.other_club, "External Filter Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(self.same_club_other_court)
        self.other_booking = self.create_booking(self.other_court)
        self.transaction_obj = self.create_transaction(
            self.booking,
            amount=Decimal("75.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.platform_admin,
        )
        self.same_club_other_transaction = self.create_transaction(
            self.same_club_other_booking,
            amount=Decimal("25.00"),
            payment_method=Transaction.PaymentMethod.BANK_TRANSFER,
            payment_reference="FILTER-BANK",
        )
        self.other_transaction = self.create_transaction(self.other_booking)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def create_settlement_for_transaction(self, transaction_obj):
        settlement = Settlement.objects.create(
            club=transaction_obj.club,
            court=transaction_obj.court,
            collected_by=transaction_obj.created_by,
            period_start=self.time_at(8),
            period_end=self.time_at(14),
            status=Settlement.Status.SETTLED,
            total_amount=transaction_obj.amount,
            transaction_count=1,
            created_by=self.platform_admin,
            settled_by=self.platform_admin,
            settled_at=self.time_at(14),
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=transaction_obj,
            amount=transaction_obj.amount,
        )
        return settlement

    def test_filter_by_booking(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"booking": self.booking.id},
        )

        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})

    def test_filter_by_court(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})

    def test_filter_by_payment_method(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"payment_method": Transaction.PaymentMethod.BANK_TRANSFER},
        )

        self.assertEqual(self.list_ids(response), {self.same_club_other_transaction.id})

    def test_filter_by_date(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date": timezone.localdate(self.transaction_obj.created).isoformat()},
        )

        self.assertIn(self.transaction_obj.id, self.list_ids(response))
        self.assertNotIn(self.other_transaction.id, self.list_ids(response))

    def test_filter_by_date_from_and_date_to(self):
        date_from = (self.transaction_obj.created - timedelta(minutes=1)).isoformat()
        date_to = (self.transaction_obj.created + timedelta(minutes=1)).isoformat()

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date_from": date_from, "date_to": date_to},
        )

        self.assertIn(self.transaction_obj.id, self.list_ids(response))
        self.assertIn(self.same_club_other_transaction.id, self.list_ids(response))
        self.assertNotIn(self.other_transaction.id, self.list_ids(response))

    def test_date_only_range_filters_transaction_created_calendar_day(self):
        selected_start = timezone.datetime(
            2026,
            8,
            17,
            0,
            1,
            tzinfo=timezone.get_current_timezone(),
        )
        selected_end = timezone.datetime(
            2026,
            8,
            17,
            23,
            59,
            tzinfo=timezone.get_current_timezone(),
        )
        before = timezone.datetime(
            2026,
            8,
            16,
            23,
            59,
            tzinfo=timezone.get_current_timezone(),
        )
        after = timezone.datetime(
            2026,
            8,
            18,
            0,
            0,
            tzinfo=timezone.get_current_timezone(),
        )
        created_in_range = self.create_transaction(self.booking)
        created_late_in_range = self.create_transaction(
            self.booking,
            payment_reference="LATE-IN-RANGE",
        )
        created_before = self.create_transaction(
            self.booking,
            payment_reference="BEFORE-RANGE",
        )
        created_after = self.create_transaction(
            self.booking,
            payment_reference="AFTER-RANGE",
        )
        Transaction.objects.filter(pk=created_in_range.pk).update(
            created=selected_start
        )
        Transaction.objects.filter(pk=created_late_in_range.pk).update(
            created=selected_end
        )
        Transaction.objects.filter(pk=created_before.pk).update(created=before)
        Transaction.objects.filter(pk=created_after.pk).update(created=after)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date_from": "2026-08-17", "date_to": "2026-08-17"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(created_in_range.id, self.list_ids(response))
        self.assertIn(created_late_in_range.id, self.list_ids(response))
        self.assertNotIn(created_before.id, self.list_ids(response))
        self.assertNotIn(created_after.id, self.list_ids(response))

    def test_transaction_date_filter_ignores_booking_start_date(self):
        booking_in_range = self.create_booking(
            self.court,
            start_time=timezone.datetime(
                2026,
                8,
                17,
                20,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            end_time=timezone.datetime(
                2026,
                8,
                17,
                21,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            customer_phone="+201000001001",
        )
        booking_outside_range = self.create_booking(
            self.court,
            start_time=timezone.datetime(
                2026,
                8,
                20,
                20,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            end_time=timezone.datetime(
                2026,
                8,
                20,
                21,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
            customer_phone="+201000001002",
        )
        transaction_before = self.create_transaction(
            booking_in_range,
            payment_reference="BOOKING-IN-TX-BEFORE",
        )
        transaction_in_range = self.create_transaction(
            booking_outside_range,
            payment_reference="BOOKING-OUT-TX-IN",
        )
        Transaction.objects.filter(pk=transaction_before.pk).update(
            created=timezone.datetime(
                2026,
                8,
                16,
                14,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
        )
        Transaction.objects.filter(pk=transaction_in_range.pk).update(
            created=timezone.datetime(
                2026,
                8,
                17,
                14,
                0,
                tzinfo=timezone.get_current_timezone(),
            )
        )

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date_from": "2026-08-17", "date_to": "2026-08-17"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(transaction_in_range.id, self.list_ids(response))
        self.assertNotIn(transaction_before.id, self.list_ids(response))

    def test_invalid_date_filter_returns_400(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date": "not-a-date"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_datetime_filter_returns_400(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date_from": "not-a-datetime"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_created_by(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"created_by": self.platform_admin.id},
        )

        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})

    def test_filter_by_unsettled_settlement_status(self):
        self.create_settlement_for_transaction(self.same_club_other_transaction)
        cancelled = self.create_transaction(
            self.booking,
            amount=Decimal("10.00"),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Cancelled filter row",
        )

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement_status": "unsettled"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})
        self.assertNotIn(cancelled.id, self.list_ids(response))

    def test_filter_by_settled_settlement_status(self):
        self.create_settlement_for_transaction(self.same_club_other_transaction)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement_status": "settled"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.same_club_other_transaction.id})

    def test_settlement_status_combines_with_existing_filters(self):
        self.create_settlement_for_transaction(self.same_club_other_transaction)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {
                "settlement_status": "settled",
                "court": self.same_club_other_court.id,
                "payment_method": Transaction.PaymentMethod.BANK_TRANSFER,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.same_club_other_transaction.id})

    def test_invalid_settlement_status_returns_field_error(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement_status": "pending"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "settlement_status")
        self.assertEqual(
            response.data["field_errors"]["settlement_status"][0]["code"],
            "INVALID_SETTLEMENT_STATUS",
        )

    def test_filters_respect_staff_assigned_court_scope(self):
        staff_transaction = self.create_transaction(
            self.booking,
            amount=Decimal("15.00"),
            created_by=self.staff,
            payment_reference="STAFF-OWN",
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date": timezone.localdate(staff_transaction.created).isoformat()},
        )

        self.assertEqual(self.list_ids(response), {staff_transaction.id})
        self.assertNotIn(self.transaction_obj.id, self.list_ids(response))
        self.assertNotIn(self.same_club_other_transaction.id, self.list_ids(response))

    def test_club_query_param_does_not_control_club_scoped_filtering(self):
        response = self.client.get(
            self.transaction_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.transaction_obj.id, self.same_club_other_transaction.id},
        )
        self.assertNotIn(self.other_transaction.id, self.list_ids(response))

    def test_search_matches_customer_name_phone_variants_and_reference(self):
        named = self.create_transaction(
            self.create_booking(
                self.court,
                customer_name="Ahmed Hassan",
                customer_phone="+201012345678",
                start_time=self.time_at(10),
                end_time=self.time_at(11),
            ),
            created_by=self.platform_admin,
            payment_reference="IPN-882192",
        )
        other = self.create_transaction(
            self.create_booking(
                self.court,
                customer_name="Mona Ali",
                customer_phone="+201000000088",
                start_time=self.time_at(11),
                end_time=self.time_at(12),
            ),
            created_by=self.platform_admin,
            payment_reference="OTHER-REF",
        )
        other_club = self.create_transaction(
            self.other_booking,
            created_by=self.platform_admin,
            payment_reference="IPN-882192-OTHER",
        )

        name_response = self.client.get(
            self.transaction_list_url(self.club),
            {"search": "Ahmed"},
        )
        phone_response = self.client.get(
            self.transaction_list_url(self.club),
            {"search": "01012345678"},
        )
        reference_response = self.client.get(
            self.transaction_list_url(self.club),
            {"search": "8821"},
        )

        self.assertEqual(self.list_ids(name_response), {named.id})
        self.assertEqual(self.list_ids(phone_response), {named.id})
        self.assertEqual(self.list_ids(reference_response), {named.id})
        self.assertNotIn(other.id, self.list_ids(name_response))
        self.assertNotIn(other_club.id, self.list_ids(name_response))
        self.assertNotIn(other_club.id, self.list_ids(reference_response))

    def test_search_respects_staff_self_only_scope(self):
        visible = self.create_transaction(
            self.booking,
            created_by=self.staff,
            payment_reference="STAFF-SEARCH-OWN",
        )
        hidden_same_name = self.create_transaction(
            self.create_booking(
                self.court,
                customer_name="Existing Customer",
                customer_phone="+201000000001",
                start_time=self.time_at(12),
                end_time=self.time_at(13),
            ),
            created_by=self.platform_admin,
            payment_reference="STAFF-SEARCH-HIDDEN",
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"search": "Existing Customer"},
        )

        self.assertEqual(self.list_ids(response), {visible.id})
        self.assertNotIn(hidden_same_name.id, self.list_ids(response))

    def test_filter_by_settlement_is_scoped_and_does_not_leak(self):
        club_settlement = self.create_settlement_for_transaction(self.transaction_obj)
        other_settlement = self.create_settlement_for_transaction(
            self.other_transaction
        )

        matching = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement": club_settlement.id},
        )
        wrong = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement": 999999},
        )
        other_club = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement": other_settlement.id},
        )

        self.assertEqual(self.list_ids(matching), {self.transaction_obj.id})
        self.assertEqual(self.list_ids(wrong), set())
        self.assertEqual(self.list_ids(other_club), set())

        staff_tx = self.create_transaction(
            self.booking,
            amount=Decimal("15.00"),
            created_by=self.staff,
            payment_reference="STAFF-SETTLEMENT-OWN",
        )
        staff_settlement = self.create_settlement_for_transaction(staff_tx)
        self.client.force_authenticate(user=self.staff)
        staff_own = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement": staff_settlement.id},
        )
        staff_other = self.client.get(
            self.transaction_list_url(self.club),
            {"settlement": club_settlement.id},
        )
        self.assertEqual(self.list_ids(staff_own), {staff_tx.id})
        self.assertEqual(self.list_ids(staff_other), set())

    def test_ordering_created_is_deterministic_with_id_tiebreaker(self):
        first = self.transaction_obj
        second = self.same_club_other_transaction
        shared_created = timezone.now()
        Transaction.objects.filter(pk__in=[first.pk, second.pk]).update(
            created=shared_created
        )

        newest = self.client.get(
            self.transaction_list_url(self.club),
            {"ordering": "-created"},
        )
        oldest = self.client.get(
            self.transaction_list_url(self.club),
            {"ordering": "created"},
        )
        default = self.client.get(self.transaction_list_url(self.club))

        newest_ids = [item["id"] for item in newest.data["results"]]
        oldest_ids = [item["id"] for item in oldest.data["results"]]
        default_ids = [item["id"] for item in default.data["results"]]
        self.assertEqual(newest_ids, sorted([first.id, second.id], reverse=True))
        self.assertEqual(oldest_ids, sorted([first.id, second.id]))
        self.assertEqual(default_ids, newest_ids)


class TransactionCentralizedAccessTests(TransactionAPITestCase):
    def test_transaction_route_resolves_to_viewset(self):
        match = resolve("/api/v1/clubs/example-club/transactions/")

        self.assertIs(match.func.cls, TransactionViewSet)

    def test_transaction_viewset_uses_django_filter_backend(self):
        self.assertEqual(TransactionViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(TransactionViewSet.filterset_class, TransactionFilter)

    def test_transaction_app_does_not_define_permissions_module(self):
        permissions_path = (
            Path(__file__).resolve().parents[2]
            / "apps"
            / "transactions"
            / "permissions.py"
        )

        self.assertFalse(permissions_path.exists())

    def test_transaction_code_uses_centralized_access_and_no_removed_assignment(self):
        repo_root = Path(__file__).resolve().parents[2]
        transaction_files = [
            repo_root / "apps" / "transactions" / "filters.py",
            repo_root / "apps" / "transactions" / "services.py",
            repo_root / "apps" / "transactions" / "serializers.py",
            repo_root / "apps" / "transactions" / "views.py",
        ]
        combined = "\n".join(path.read_text() for path in transaction_files)

        self.assertIn("can_create_transaction_for_booking", combined)
        self.assertIn("scoped_transactions_queryset", combined)
        self.assertNotIn("ClubMembership", combined)
        self.assertNotIn("ClubAccessContext", combined)
        self.assertNotIn("CourtStaffAssignment", combined)
        self.assertNotIn(".role", combined)

    def test_transaction_viewset_does_not_manually_parse_filter_query_params(self):
        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "transactions" / "views.py").read_text()

        self.assertNotIn("request.query_params", view_source)
        self.assertNotIn("parse_date_param", view_source)
        self.assertNotIn("parse_datetime_param", view_source)

    def test_schema_and_docs_return_200(self):
        schema_response = self.client.get(reverse("schema"))
        docs_response = self.client.get(reverse("swagger-ui"))
        schema = schema_response.content.decode()
        schema_doc = yaml.safe_load(schema)

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "/api/v1/clubs/{club_slug}/transactions/",
            schema,
        )
        transaction_create_responses = schema_doc["paths"][
            "/api/v1/clubs/{club_slug}/transactions/"
        ]["post"]["responses"]
        self.assertIn("201", transaction_create_responses)
        self.assertIn("200", transaction_create_responses)
        self.assertEqual(
            transaction_create_responses["201"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/TransactionDetail",
        )
        self.assertEqual(
            transaction_create_responses["200"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/TransactionDetail",
        )
        attempt_fields = schema_doc["components"]["schemas"]["TransactionAttemptList"][
            "properties"
        ]
        self.assertEqual(attempt_fields["resolved_transaction"]["type"], "integer")
        self.assertTrue(attempt_fields["resolved_transaction"]["nullable"])
        self.assertIn("booking_customer_name", schema)
        self.assertIn("booking_customer_phone", schema)
        self.assertIn("booking_start_time", schema)
        self.assertIn("booking_end_time", schema)
        self.assertIn("settlement", schema)


class TransactionQueryScalingTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("tx-query-admin")
        self.club = self.create_club("Tx Query Club", slug="tx-query")
        self.court = self.create_court(self.club, "Tx Query Court")
        self.client.force_authenticate(user=self.platform_admin)

    def test_list_query_count_does_not_grow_with_customer_fields_or_search(self):
        first_booking = self.create_booking(
            self.court,
            customer_name="Query Customer",
            customer_phone="+201012345678",
            start_time=self.time_at(8),
            end_time=self.time_at(9),
        )
        self.create_transaction(
            first_booking,
            created_by=self.platform_admin,
            payment_reference="TX-Q-1",
        )

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(self.transaction_list_url(self.club))

        for index in range(12):
            booking = self.create_booking(
                self.court,
                customer_name=f"Query Customer {index}",
                customer_phone=f"+2010123456{index:02d}",
                start_time=self.time_at(9 + (index % 8)),
                end_time=self.time_at(10 + (index % 8)),
            )
            self.create_transaction(
                booking,
                created_by=self.platform_admin,
                payment_reference=f"TX-Q-{index + 2}",
            )

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(self.transaction_list_url(self.club))
        with CaptureQueriesContext(connection) as search:
            search_response = self.client.get(
                self.transaction_list_url(self.club),
                {"search": "Query Customer"},
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(search_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["count"], 1)
        self.assertEqual(second_response.data["count"], 13)
        self.assertEqual(
            first_response.data["results"][0]["booking_customer_name"],
            "Query Customer",
        )
        self.assertEqual(len(first), len(second))
        self.assertEqual(len(second), len(search))
