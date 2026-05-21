from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.urls import resolve, reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.transactions.filters import TransactionFilter
from apps.transactions.models import Transaction
from apps.transactions.services import (
    DUPLICATE_PAYMENT_REFERENCE_MESSAGE,
    create_booking_transaction,
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
            "city": "Assiut",
            "area": "Downtown",
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
        self.transaction_obj = self.create_transaction(self.booking)
        self.same_club_other_transaction = self.create_transaction(
            self.same_club_other_booking
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
            {self.transaction_obj.id, self.same_club_other_transaction.id},
        )
        self.assertNotIn(self.other_transaction.id, self.list_ids(response))

    def test_owner_can_list_transactions_in_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.transaction_obj.id, self.same_club_other_transaction.id},
        )

    def test_manager_can_list_transactions_in_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.transaction_obj.id, self.same_club_other_transaction.id},
        )

    def test_staff_can_list_transactions_for_assigned_court_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.transaction_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})
        self.assertNotIn(self.same_club_other_transaction.id, self.list_ids(response))

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

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)

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
        self.assertIn("amount", response.data)

    def test_can_create_partial_payment(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="100.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["amount"], "100.00")

    def test_can_create_second_payment_up_to_remaining_amount(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_transaction(self.club, self.booking, amount="200.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class TransactionBookingConfirmationTests(TransactionAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("confirm-admin")
        self.club = self.create_club("Confirm Club", slug="confirm-club")
        self.court = self.create_court(self.club, "Confirm Court")
        self.client.force_authenticate(user=self.platform_admin)

    def assert_status_rejects_transaction(self, booking_status):
        booking = self.create_booking(self.court, status=booking_status)

        response = self.post_transaction(self.club, booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)

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
        self.assertIn("payment_reference", response.data)

    def test_bank_transfer_requires_payment_reference_when_court_requires_it(self):
        response = self.post_transaction(
            self.club,
            self.booking,
            payment_method=Transaction.PaymentMethod.BANK_TRANSFER,
            payment_reference="",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("payment_reference", response.data)

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
            response.data["payment_reference"][0],
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

    def test_filters_respect_staff_assigned_court_scope(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.transaction_list_url(self.club),
            {"date": timezone.localdate(self.transaction_obj.created).isoformat()},
        )

        self.assertEqual(self.list_ids(response), {self.transaction_obj.id})
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


class TransactionCentralizedAccessTests(TransactionAPITestCase):
    def test_transaction_route_resolves_to_viewset(self):
        match = resolve("/api/clubs/example-club/transactions/")

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

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "/api/clubs/{club_slug}/transactions/", schema_response.content.decode()
        )
