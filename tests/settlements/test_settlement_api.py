from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import yaml
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.filters import SettlementFilter
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import get_current_unsettled_transactions
from apps.settlements.views import SettlementViewSet
from apps.transactions.models import Transaction


class SettlementAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="settlement-admin") -> User:
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
        is_active: bool = True,
        **extra_fields,
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
            is_active=is_active,
            **extra_fields,
        )

    def time_at(self, hour: int):
        day_start = timezone.datetime(
            2026,
            7,
            2,
            0,
            0,
            tzinfo=timezone.get_current_timezone(),
        )
        return day_start + timedelta(hours=hour)

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        start_time = extra_fields.pop("start_time", self.time_at(20))
        end_time = extra_fields.pop("end_time", self.time_at(21))
        data = {
            "club": court.club,
            "court": court,
            "customer_name": "Settlement Customer",
            "customer_phone": "+201000000001",
            "start_time": start_time,
            "end_time": end_time,
            "total_price": Decimal("300.00"),
            "status": Booking.Status.CONFIRMED,
            "source": Booking.Source.MANUAL,
        }
        data.update(extra_fields)
        return Booking.objects.create(**data)

    def create_transaction(self, booking: Booking, **extra_fields) -> Transaction:
        created = extra_fields.pop("created", self.time_at(12))
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        transaction_obj = Transaction.objects.create(**data)
        Transaction.objects.filter(pk=transaction_obj.pk).update(created=created)
        transaction_obj.refresh_from_db()
        return transaction_obj

    def create_settlement(self, club: Club, **extra_fields) -> Settlement:
        data = {
            "club": club,
            "period_start": self.time_at(10),
            "period_end": self.time_at(14),
            "status": Settlement.Status.PENDING,
            "total_amount": Decimal("50.00"),
            "transaction_count": 1,
        }
        data.update(extra_fields)
        return Settlement.objects.create(**data)

    def settlement_list_url(self, club):
        return reverse("club-settlement-list", kwargs={"club_slug": club.slug})

    def settlement_preview_url(self, club):
        return reverse("club-settlement-preview", kwargs={"club_slug": club.slug})

    def settlement_unsettled_summary_url(self, club):
        return reverse(
            "club-settlement-unsettled-summary",
            kwargs={"club_slug": club.slug},
        )

    def settlement_detail_url(self, club, settlement_obj):
        return reverse(
            "club-settlement-detail",
            kwargs={"club_slug": club.slug, "pk": settlement_obj.pk},
        )

    def settlement_mark_settled_url(self, club, settlement_obj):
        return reverse(
            "club-settlement-mark-settled",
            kwargs={"club_slug": club.slug, "pk": settlement_obj.pk},
        )

    def booking_action_url(self, club, booking, action):
        return reverse(
            f"club-booking-{action}",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def settlement_payload(self, **extra_fields):
        data = {}
        default_collector = getattr(self, "staff", None) or getattr(
            self,
            "collector",
            None,
        )
        if default_collector is not None:
            data["collected_by"] = default_collector.id
        data.update(extra_fields)
        return data

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


class SettlementModelTests(SettlementAPITestCase):
    def setUp(self):
        self.club = self.create_club("Model Club", slug="settlement-model")
        self.court = self.create_court(self.club, "Model Court")
        self.booking = self.create_booking(self.court)
        self.transaction_obj = self.create_transaction(self.booking)

    def test_settlement_can_be_created(self):
        settlement = self.create_settlement(self.club, court=self.court)

        self.assertEqual(settlement.club, self.club)
        self.assertEqual(settlement.status, Settlement.Status.PENDING)

    def test_settlement_line_links_one_transaction(self):
        settlement = self.create_settlement(self.club, court=self.court)
        line = SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=self.transaction_obj,
            amount=self.transaction_obj.amount,
        )

        self.assertEqual(line.transaction, self.transaction_obj)
        self.assertEqual(self.transaction_obj.settlement_line, line)

    def test_same_transaction_cannot_be_linked_to_two_settlements(self):
        first = self.create_settlement(self.club, court=self.court)
        second = self.create_settlement(
            self.club,
            court=self.court,
            period_start=self.time_at(15),
            period_end=self.time_at(16),
        )
        SettlementTransaction.objects.create(
            settlement=first,
            transaction=self.transaction_obj,
            amount=self.transaction_obj.amount,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            SettlementTransaction.objects.create(
                settlement=second,
                transaction=self.transaction_obj,
                amount=self.transaction_obj.amount,
            )

    def test_period_start_must_be_before_period_end(self):
        settlement = Settlement(
            club=self.club,
            court=self.court,
            period_start=self.time_at(14),
            period_end=self.time_at(10),
            total_amount=Decimal("0.00"),
            transaction_count=0,
        )

        with self.assertRaises(DjangoValidationError):
            settlement.full_clean()

    def test_line_amount_must_be_positive(self):
        settlement = self.create_settlement(self.club, court=self.court)
        line = SettlementTransaction(
            settlement=settlement,
            transaction=self.transaction_obj,
            amount=Decimal("0.00"),
        )

        with self.assertRaises(DjangoValidationError):
            line.full_clean()


class SettlementAccessTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("access-admin")
        self.owner = self.create_user("access-owner")
        self.manager = self.create_user("access-manager")
        self.staff = self.create_user("access-staff")
        self.other_user = self.create_user("access-other")
        self.club = self.create_club("Access Club", slug="settlement-access")
        self.other_club = self.create_club(
            "Other Access Club",
            slug="other-settlement-access",
        )
        self.court = self.create_court(self.club, "Access Court")
        self.other_court = self.create_court(self.other_club, "Other Access Court")
        self.booking = self.create_booking(self.court)
        self.create_transaction(self.booking, created_by=self.staff)
        self.settlement = self.create_settlement(self.club, court=self.court)
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.manager_membership = self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
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

    def test_anonymous_cannot_access_settlements(self):
        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_list_preview_create_and_settle(self):
        self.client.force_authenticate(user=self.platform_admin)

        list_response = self.client.get(self.settlement_list_url(self.club))
        preview_response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )
        create_response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )
        settle_response = self.client.post(
            self.settlement_mark_settled_url(self.club, self.settlement),
            {},
            format="json",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(preview_response.status_code, status.HTTP_200_OK)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(settle_response.status_code, status.HTTP_200_OK)

    def test_owner_can_access_settlements(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_manager_can_access_settlements_when_flag_enabled(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_manager_without_flag_can_view_own_settlements_only(self):
        self.manager_membership.manager_can_settle_transactions = False
        self.manager_membership.save(update_fields=["manager_can_settle_transactions"])
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_can_view_own_settlements_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unrelated_club_member_cannot_access_selected_club_settlements(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SettlementPreviewCreateTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("preview-admin")
        self.owner = self.create_user("preview-owner")
        self.manager = self.create_user("preview-manager")
        self.manager_without_permission = self.create_user("preview-manager-denied")
        self.staff = self.create_user(
            "preview-staff",
            first_name="Ahmed",
            last_name="Ali",
        )
        self.manager_collector = self.create_user("preview-manager-collector")
        self.manager_denied_collector = self.create_user(
            "preview-manager-denied-collector"
        )
        self.other_staff = self.create_user("preview-other-staff")
        self.inactive_staff = self.create_user("preview-inactive-staff")
        self.club = self.create_club("Preview Club", slug="settlement-preview")
        self.manager_club = self.create_club(
            "Manager Settlement Club",
            slug="manager-settlement-preview",
        )
        self.manager_denied_club = self.create_club(
            "Manager Denied Settlement Club",
            slug="manager-denied-settlement-preview",
        )
        self.other_club = self.create_club("Other Preview Club", slug="other-preview")
        self.court = self.create_court(self.club, "Preview Court")
        self.manager_court = self.create_court(self.manager_club, "Manager Court")
        self.manager_denied_court = self.create_court(
            self.manager_denied_club,
            "Manager Denied Court",
        )
        self.same_club_other_court = self.create_court(self.club, "Preview Other Court")
        self.other_court = self.create_court(self.other_club, "Other Preview Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.manager,
            self.manager_club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        self.create_membership(
            self.manager_without_permission,
            self.manager_denied_club,
            ClubMembership.Role.MANAGER,
        )
        self.create_membership(
            self.manager_collector,
            self.manager_club,
            ClubMembership.Role.STAFF,
            court=self.manager_court,
        )
        self.create_membership(
            self.manager_denied_collector,
            self.manager_denied_club,
            ClubMembership.Role.STAFF,
            court=self.manager_denied_court,
        )
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.other_staff,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )
        self.create_membership(
            self.inactive_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
            is_active=False,
        )
        self.booking = self.create_booking(self.court, customer_phone="+201000000010")
        self.second_booking = self.create_booking(
            self.court,
            customer_phone="+201000000011",
            start_time=self.time_at(21),
            end_time=self.time_at(22),
        )
        self.other_court_booking = self.create_booking(
            self.same_club_other_court,
            customer_phone="+201000000012",
            start_time=self.time_at(23),
            end_time=self.time_at(24),
        )
        self.other_club_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000000013",
            start_time=self.time_at(25),
            end_time=self.time_at(26),
        )
        self.manager_booking = self.create_booking(
            self.manager_court,
            customer_phone="+201000000014",
            start_time=self.time_at(27),
            end_time=self.time_at(28),
        )
        self.manager_denied_booking = self.create_booking(
            self.manager_denied_court,
            customer_phone="+201000000015",
            start_time=self.time_at(29),
            end_time=self.time_at(30),
        )
        self.first_transaction = self.create_transaction(
            self.booking,
            amount=Decimal("50.00"),
            created=self.time_at(11),
            payment_reference="PREVIEW-1",
            created_by=self.staff,
        )
        self.second_transaction = self.create_transaction(
            self.second_booking,
            amount=Decimal("75.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-2",
            created_by=self.staff,
        )
        self.other_court_transaction = self.create_transaction(
            self.other_court_booking,
            amount=Decimal("25.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-OTHER-COURT",
            created_by=self.staff,
        )
        self.other_club_transaction = self.create_transaction(
            self.other_club_booking,
            amount=Decimal("90.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-OTHER-CLUB",
            created_by=self.other_staff,
        )
        self.manager_transaction = self.create_transaction(
            self.manager_booking,
            amount=Decimal("60.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-MANAGER",
            created_by=self.manager_collector,
        )
        self.manager_denied_transaction = self.create_transaction(
            self.manager_denied_booking,
            amount=Decimal("60.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-MANAGER-DENIED",
            created_by=self.manager_denied_collector,
        )
        self.owner_booking = self.create_booking(
            self.court,
            customer_phone="+201000000018",
            start_time=self.time_at(35),
            end_time=self.time_at(36),
        )
        self.owner_second_booking = self.create_booking(
            self.same_club_other_court,
            customer_phone="+201000000019",
            start_time=self.time_at(37),
            end_time=self.time_at(38),
        )
        self.owner_settled_booking = self.create_booking(
            self.court,
            customer_phone="+201000000020",
            start_time=self.time_at(39),
            end_time=self.time_at(40),
        )
        self.owner_cancelled_booking = self.create_booking(
            self.court,
            customer_phone="+201000000021",
            start_time=self.time_at(41),
            end_time=self.time_at(42),
        )
        self.platform_admin_booking = self.create_booking(
            self.court,
            customer_phone="+201000000022",
            start_time=self.time_at(43),
            end_time=self.time_at(44),
        )
        self.manager_self_booking = self.create_booking(
            self.manager_court,
            customer_phone="+201000000023",
            start_time=self.time_at(45),
            end_time=self.time_at(46),
        )
        self.manager_denied_self_booking = self.create_booking(
            self.manager_denied_court,
            customer_phone="+201000000024",
            start_time=self.time_at(47),
            end_time=self.time_at(48),
        )
        self.owner_transaction = self.create_transaction(
            self.owner_booking,
            amount=Decimal("80.00"),
            created=self.time_at(13),
            payment_reference="PREVIEW-OWNER-SELF",
            created_by=self.owner,
        )
        self.owner_second_transaction = self.create_transaction(
            self.owner_second_booking,
            amount=Decimal("90.00"),
            created=self.time_at(14),
            payment_reference="PREVIEW-OWNER-SELF-2",
            created_by=self.owner,
        )
        self.owner_settled_transaction = self.create_transaction(
            self.owner_settled_booking,
            amount=Decimal("100.00"),
            created=self.time_at(15),
            payment_reference="PREVIEW-OWNER-SETTLED",
            created_by=self.owner,
        )
        self.owner_cancelled_transaction = self.create_transaction(
            self.owner_cancelled_booking,
            amount=Decimal("110.00"),
            created=self.time_at(16),
            payment_reference="PREVIEW-OWNER-CANCELLED",
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Owner correction",
            created_by=self.owner,
        )
        self.platform_admin_transaction = self.create_transaction(
            self.platform_admin_booking,
            amount=Decimal("120.00"),
            created=self.time_at(17),
            payment_reference="PREVIEW-ADMIN-SELF",
            created_by=self.platform_admin,
        )
        self.manager_self_transaction = self.create_transaction(
            self.manager_self_booking,
            amount=Decimal("130.00"),
            created=self.time_at(18),
            payment_reference="PREVIEW-MANAGER-SELF",
            created_by=self.manager,
        )
        self.manager_denied_self_transaction = self.create_transaction(
            self.manager_denied_self_booking,
            amount=Decimal("140.00"),
            created=self.time_at(19),
            payment_reference="PREVIEW-MANAGER-DENIED-SELF",
            created_by=self.manager_without_permission,
        )
        self.already_settled = self.create_transaction(
            self.booking,
            amount=Decimal("30.00"),
            created=self.time_at(13),
            payment_reference="PREVIEW-SETTLED",
            created_by=self.staff,
        )
        self.cancelled_transaction = self.create_transaction(
            self.booking,
            amount=Decimal("40.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-CANCELED",
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Wrong settlement amount",
            created_by=self.staff,
        )
        settlement = self.create_settlement(
            self.club,
            court=self.court,
            collected_by=self.staff,
            total_amount=self.already_settled.amount,
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=self.already_settled,
            amount=self.already_settled.amount,
        )
        owner_settlement = self.create_settlement(
            self.club,
            court=self.court,
            collected_by=self.owner,
            total_amount=self.owner_settled_transaction.amount,
        )
        SettlementTransaction.objects.create(
            settlement=owner_settlement,
            transaction=self.owner_settled_transaction,
            amount=self.owner_settled_transaction.amount,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_preview_returns_correct_count_and_total(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["total_amount"], "150.00")
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertEqual(response.data["collected_by_name"], "Ahmed Ali")
        self.assertFalse(response.data["is_self_preview"])
        self.assertTrue(response.data["can_approve"])
        self.assertFalse(response.data["approval_required"])
        self.assertEqual(
            response.data["totals_by_payment_method"][Transaction.PaymentMethod.CASH],
            "150.00",
        )
        self.assertEqual(
            {item["id"] for item in response.data["transactions"]},
            {
                self.first_transaction.id,
                self.second_transaction.id,
                self.other_court_transaction.id,
            },
        )
        preview_item = next(
            item
            for item in response.data["transactions"]
            if item["id"] == self.first_transaction.id
        )
        self.assertEqual(preview_item["booking_customer_name"], "Settlement Customer")
        self.assertEqual(
            preview_item["booking_customer_phone"],
            self.booking.customer_phone,
        )
        self.assertIsNotNone(preview_item["booking_start_time"])
        self.assertIsNotNone(preview_item["booking_end_time"])

    def test_preview_without_collected_by_defaults_to_request_user(self):
        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.platform_admin.id)
        self.assertTrue(response.data["is_self_preview"])

    def test_preview_does_not_require_period_start_or_period_end(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_preview_excludes_cancelled_transactions(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transaction_ids = {item["id"] for item in response.data["transactions"]}
        self.assertNotIn(self.cancelled_transaction.id, transaction_ids)

    def test_preview_excludes_already_settled_transactions(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transaction_ids = {item["id"] for item in response.data["transactions"]}
        self.assertNotIn(self.already_settled.id, transaction_ids)

    def test_preview_supports_optional_court_filter(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(
                collected_by=self.staff.id,
                court=self.same_club_other_court.id,
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["court"], self.same_club_other_court.id)
        self.assertEqual(response.data["court_name"], self.same_club_other_court.name)
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["total_amount"], "150.00")
        self.assertEqual(
            {item["id"] for item in response.data["transactions"]},
            {
                self.first_transaction.id,
                self.second_transaction.id,
                self.other_court_transaction.id,
            },
        )

    def test_preview_does_not_create_or_mutate_records(self):
        before_settlements = Settlement.objects.count()
        before_lines = SettlementTransaction.objects.count()
        before_audits = AuditLog.objects.count()
        transaction_ids = [self.first_transaction.id, self.second_transaction.id]
        before_linked_ids = set(
            SettlementTransaction.objects.filter(
                transaction_id__in=transaction_ids
            ).values_list("transaction_id", flat=True)
        )

        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Settlement.objects.count(), before_settlements)
        self.assertEqual(SettlementTransaction.objects.count(), before_lines)
        self.assertEqual(AuditLog.objects.count(), before_audits)
        after_linked_ids = set(
            SettlementTransaction.objects.filter(
                transaction_id__in=transaction_ids
            ).values_list("transaction_id", flat=True)
        )
        self.assertEqual(after_linked_ids, before_linked_ids)

    def test_preview_excludes_transactions_created_by_other_users(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transaction_ids = {item["id"] for item in response.data["transactions"]}
        self.assertNotIn(self.other_club_transaction.id, transaction_ids)

    def test_preview_respects_selected_club_scope(self):
        response = self.client.get(
            self.settlement_preview_url(self.other_club),
            self.settlement_payload(collected_by=self.other_staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 1)
        self.assertEqual(response.data["total_amount"], "90.00")

    def test_preview_rejects_collected_by_from_another_club(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.other_staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "collected_by")

    def test_preview_rejects_inactive_membership_user(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.inactive_staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "collected_by")

    def test_staff_can_preview_himself_without_collected_by(self):
        before_settlements = Settlement.objects.count()
        before_lines = SettlementTransaction.objects.count()
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertTrue(response.data["is_self_preview"])
        self.assertFalse(response.data["can_approve"])
        self.assertTrue(response.data["approval_required"])
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["total_amount"], "150.00")
        self.assertEqual(Settlement.objects.count(), before_settlements)
        self.assertEqual(SettlementTransaction.objects.count(), before_lines)

    def test_preview_uses_signed_booking_refund_amounts(self):
        self.create_transaction(
            self.second_booking,
            transaction_type=Transaction.Type.REFUND,
            amount=Decimal("-25.00"),
            created=self.time_at(13),
            payment_reference="PREVIEW-REFUND",
            created_by=self.staff,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 4)
        self.assertEqual(response.data["booking_payments"], "150.00")
        self.assertEqual(response.data["booking_refunds"], "-25.00")
        self.assertEqual(response.data["total_amount"], "125.00")
        refund_item = next(
            item for item in response.data["transactions"] if item["kind"] == "REFUND"
        )
        self.assertEqual(refund_item["amount"], "-25.00")

    def test_staff_cannot_preview_another_user(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.owner.id),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_preview_any_active_club_user(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertFalse(response.data["is_self_preview"])
        self.assertTrue(response.data["can_approve"])
        self.assertFalse(response.data["approval_required"])

    def test_create_settlement_from_unsettled_transactions(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(
                collected_by=self.staff.id,
                notes="Collected cash from Ahmed",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        settlement = Settlement.objects.get(id=response.data["id"])
        self.assertEqual(settlement.status, Settlement.Status.SETTLED)
        self.assertIsNone(settlement.court)
        self.assertEqual(settlement.collected_by, self.staff)
        self.assertEqual(settlement.created_by, self.platform_admin)
        self.assertEqual(settlement.settled_by, self.platform_admin)
        self.assertIsNotNone(settlement.settled_at)
        self.assertEqual(settlement.period_start, self.first_transaction.created)
        self.assertLessEqual(settlement.period_end, timezone.now())
        self.assertEqual(settlement.total_amount, Decimal("150.00"))
        self.assertEqual(settlement.transaction_count, 3)
        self.assertEqual(settlement.notes, "Collected cash from Ahmed")
        self.assertEqual(settlement.lines.count(), 3)
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertEqual(response.data["collected_by_name"], "Ahmed Ali")

    def test_create_excludes_already_settled_transactions(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction_ids = set(
            Settlement.objects.get(id=response.data["id"]).lines.values_list(
                "transaction_id",
                flat=True,
            )
        )
        self.assertNotIn(self.already_settled.id, transaction_ids)
        self.assertNotIn(self.cancelled_transaction.id, transaction_ids)

    def test_preview_defaults_to_request_user_and_creates_nothing(self):
        before_settlements = Settlement.objects.count()
        before_lines = SettlementTransaction.objects.count()
        before_audits = AuditLog.objects.count()

        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.platform_admin.id)
        self.assertEqual(Settlement.objects.count(), before_settlements)
        self.assertEqual(SettlementTransaction.objects.count(), before_lines)
        self.assertEqual(AuditLog.objects.count(), before_audits)

    def test_create_requires_collected_by(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "collected_by")

    def test_create_does_not_require_period_start_or_period_end(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_creation_with_no_unsettled_transactions_returns_400(self):
        for transaction_obj in (
            self.first_transaction,
            self.second_transaction,
            self.other_court_transaction,
        ):
            SettlementTransaction.objects.create(
                settlement=self.create_settlement(
                    self.club,
                    collected_by=self.staff,
                    period_start=self.time_at(15),
                    period_end=self.time_at(16),
                    total_amount=transaction_obj.amount,
                ),
                transaction=transaction_obj,
                amount=transaction_obj.amount,
            )

        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "NO_UNSETTLED_TRANSACTIONS")
        self.assertNotIn("transactions", response.data)

    def test_owner_can_settle_another_user(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_can_preview_settlement_for_himself(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.owner.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.owner.id)
        self.assertEqual(response.data["transaction_count"], 2)
        self.assertEqual(response.data["total_amount"], "170.00")
        self.assertTrue(response.data["is_self_preview"])
        self.assertTrue(response.data["can_approve"])
        self.assertFalse(response.data["approval_required"])
        self.assertEqual(
            {item["id"] for item in response.data["transactions"]},
            {self.owner_transaction.id, self.owner_second_transaction.id},
        )
        self.assertNotIn(
            self.owner_settled_transaction.id,
            {item["id"] for item in response.data["transactions"]},
        )
        self.assertNotIn(
            self.owner_cancelled_transaction.id,
            {item["id"] for item in response.data["transactions"]},
        )

    def test_owner_can_create_settlement_for_himself(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(
                collected_by=self.owner.id,
                notes="Owner self-settlement",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        settlement = Settlement.objects.get(id=response.data["id"])
        self.assertEqual(settlement.collected_by, self.owner)
        self.assertEqual(settlement.created_by, self.owner)
        self.assertEqual(settlement.status, Settlement.Status.SETTLED)
        self.assertEqual(settlement.settled_by, self.owner)
        self.assertIsNotNone(settlement.settled_at)
        self.assertEqual(settlement.total_amount, Decimal("170.00"))
        self.assertEqual(settlement.transaction_count, 2)
        self.assertEqual(settlement.notes, "Owner self-settlement")
        self.assertEqual(
            set(settlement.lines.values_list("transaction_id", flat=True)),
            {self.owner_transaction.id, self.owner_second_transaction.id},
        )
        self.assertNotIn(
            self.owner_settled_transaction.id,
            set(settlement.lines.values_list("transaction_id", flat=True)),
        )
        self.assertNotIn(
            self.owner_cancelled_transaction.id,
            set(settlement.lines.values_list("transaction_id", flat=True)),
        )

    def test_platform_admin_can_preview_settlement_for_himself(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(collected_by=self.platform_admin.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.platform_admin.id)
        self.assertEqual(response.data["transaction_count"], 1)
        self.assertEqual(response.data["total_amount"], "120.00")
        self.assertEqual(
            {item["id"] for item in response.data["transactions"]},
            {self.platform_admin_transaction.id},
        )

    def test_platform_admin_can_create_settlement_for_himself(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(
                collected_by=self.platform_admin.id,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        settlement = Settlement.objects.get(id=response.data["id"])
        self.assertEqual(settlement.collected_by, self.platform_admin)
        self.assertEqual(settlement.created_by, self.platform_admin)
        self.assertEqual(settlement.status, Settlement.Status.SETTLED)
        self.assertEqual(settlement.settled_by, self.platform_admin)
        self.assertIsNotNone(settlement.settled_at)
        self.assertEqual(
            set(settlement.lines.values_list("transaction_id", flat=True)),
            {self.platform_admin_transaction.id},
        )

    def test_manager_with_settlement_permission_can_settle_another_user(self):
        self.client.force_authenticate(user=self.manager)

        preview_response = self.client.get(
            self.settlement_preview_url(self.manager_club),
            self.settlement_payload(collected_by=self.manager_collector.id),
        )
        create_response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(
                collected_by=self.manager_collector.id,
            ),
            format="json",
        )

        self.assertEqual(preview_response.status_code, status.HTTP_200_OK)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

    def test_manager_with_settlement_permission_can_preview_himself(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            self.settlement_preview_url(self.manager_club),
            self.settlement_payload(collected_by=self.manager.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.manager.id)
        self.assertTrue(response.data["is_self_preview"])
        self.assertFalse(response.data["can_approve"])
        self.assertTrue(response.data["approval_required"])
        self.assertEqual(response.data["transaction_count"], 1)
        self.assertEqual(response.data["total_amount"], "130.00")

    def test_manager_with_settlement_permission_can_preview_himself_without_post(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.settlement_preview_url(self.manager_club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.manager.id)
        self.assertFalse(response.data["can_approve"])

    def test_manager_without_settlement_permission_can_preview_himself(self):
        self.client.force_authenticate(user=self.manager_without_permission)

        response = self.client.get(
            self.settlement_preview_url(self.manager_denied_club)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["collected_by"],
            self.manager_without_permission.id,
        )
        self.assertTrue(response.data["is_self_preview"])
        self.assertFalse(response.data["can_approve"])
        self.assertTrue(response.data["approval_required"])

    def test_manager_with_settlement_permission_cannot_create_for_himself(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(collected_by=self.manager.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "SELF_SETTLEMENT_APPROVAL_FORBIDDEN")
        self.assertEqual(
            response.data["message"],
            "You cannot approve your own settlement.",
        )
        self.assertNotIn("settlement", response.data)

    def test_manager_self_create_error_supports_arabic(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(collected_by=self.manager.id),
            format="json",
            HTTP_ACCEPT_LANGUAGE="ar",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "SELF_SETTLEMENT_APPROVAL_FORBIDDEN")
        self.assertEqual(
            response.data["message"],
            "لا يمكنك اعتماد التسوية الخاصة بك.",
        )

    def test_manager_without_settlement_permission_cannot_settle(self):
        self.client.force_authenticate(user=self.manager_without_permission)

        preview_response = self.client.get(
            self.settlement_preview_url(self.manager_denied_club),
            self.settlement_payload(collected_by=self.manager_denied_collector.id),
        )
        create_response = self.client.post(
            self.settlement_list_url(self.manager_denied_club),
            self.settlement_payload(
                collected_by=self.manager_denied_collector.id,
            ),
            format="json",
        )

        self.assertEqual(preview_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_with_permission_cannot_settle_owner_collector(self):
        manager_club_owner = self.create_user("preview-manager-club-owner")
        self.create_membership(
            manager_club_owner,
            self.manager_club,
            ClubMembership.Role.OWNER,
        )
        owner_booking = self.create_booking(
            self.manager_court,
            customer_phone="+201000000016",
            start_time=self.time_at(31),
            end_time=self.time_at(32),
        )
        self.create_transaction(
            owner_booking,
            amount=Decimal("10.00"),
            payment_reference="PREVIEW-MANAGER-OWNER",
            created_by=manager_club_owner,
        )
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(
                collected_by=manager_club_owner.id,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_settle_inactive_employee_by_default(self):
        inactive_manager_employee = self.create_user("preview-manager-inactive")
        self.create_membership(
            inactive_manager_employee,
            self.manager_club,
            ClubMembership.Role.STAFF,
            court=self.manager_court,
            is_active=False,
        )
        inactive_booking = self.create_booking(
            self.manager_court,
            customer_phone="+201000000017",
            start_time=self.time_at(33),
            end_time=self.time_at(34),
        )
        self.create_transaction(
            inactive_booking,
            amount=Decimal("10.00"),
            payment_reference="PREVIEW-MANAGER-INACTIVE",
            created_by=inactive_manager_employee,
        )
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(
                collected_by=inactive_manager_employee.id,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "collected_by")

    def test_manager_cannot_settle_user_from_another_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            self.settlement_list_url(self.manager_club),
            self.settlement_payload(collected_by=self.other_staff.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "collected_by")

    def test_staff_cannot_settle(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.owner.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_preview_himself_without_post(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertFalse(response.data["can_approve"])

    def test_settled_transactions_cannot_be_included_again(self):
        first_response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )
        second_response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(collected_by=self.staff.id),
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(second_response, "NO_UNSETTLED_TRANSACTIONS")
        self.assertNotIn("transactions", second_response.data)


class SettlementCurrentCustodyContractTests(SettlementAPITestCase):
    def setUp(self):
        self.owner = self.create_user("custody-owner")
        self.manager = self.create_user("custody-manager")
        self.staff = self.create_user(
            "custody-mohamed",
            first_name="Mohamed",
            last_name="Ahmed",
        )
        self.club = self.create_club("Custody Club", slug="custody-contract")
        self.court = self.create_court(
            self.club,
            "Custody Court",
            cancellation_refund_notice_days=0,
        )
        self.other_court = self.create_court(self.club, "Custody Other Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        booking = self.create_booking(self.court, customer_name="Mohamed Custody")
        self.old_payment = self.create_transaction(
            booking,
            amount=Decimal("500.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created=timezone.now() - timedelta(days=10),
            created_by=self.staff,
            payment_reference="CUSTODY-OLD",
        )
        self.recent_payment = self.create_transaction(
            booking,
            amount=Decimal("900.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            created=timezone.now(),
            created_by=self.staff,
            payment_reference="CUSTODY-RECENT",
        )
        self.recent_refund = self.create_transaction(
            booking,
            transaction_type=Transaction.Type.REFUND,
            amount=Decimal("-150.00"),
            payment_method=Transaction.PaymentMethod.BANK_TRANSFER,
            created=timezone.now(),
            created_by=self.staff,
            payment_reference="CUSTODY-REFUND",
        )
        self.cancelled_payment = self.create_transaction(
            booking,
            amount=Decimal("300.00"),
            payment_method=Transaction.PaymentMethod.OTHER,
            created=timezone.now(),
            created_by=self.staff,
            payment_reference="CUSTODY-CANCELLED",
            is_cancelled=True,
            cancelled_by=self.owner,
            cancelled_at=timezone.now(),
            cancellation_reason="Correction",
        )
        self.settled_payment = self.create_transaction(
            booking,
            amount=Decimal("400.00"),
            created=timezone.now(),
            created_by=self.staff,
            payment_reference="CUSTODY-SETTLED",
        )
        settlement = self.create_settlement(
            self.club,
            collected_by=self.staff,
            status=Settlement.Status.SETTLED,
            total_amount=self.settled_payment.amount,
            settled_by=self.owner,
            settled_at=timezone.now(),
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=self.settled_payment,
            amount=self.settled_payment.amount,
        )

    def access_for(self, user):
        return ClubAccessContext(
            request=SimpleNamespace(user=user),
            club=self.club,
        )

    def test_every_consumer_uses_the_same_exact_signed_candidate_ids(self):
        expected_ids = {
            self.old_payment.id,
            self.recent_payment.id,
            self.recent_refund.id,
        }
        for user in (self.staff, self.owner, self.manager):
            direct_ids = set(
                get_current_unsettled_transactions(
                    access=self.access_for(user),
                    collected_by=self.staff,
                ).values_list("id", flat=True)
            )
            self.assertEqual(direct_ids, expected_ids)

            self.client.force_authenticate(user=user)
            preview = self.client.get(
                self.settlement_preview_url(self.club),
                {"collected_by": self.staff.id},
            )
            self.assertEqual(preview.status_code, status.HTTP_200_OK)
            self.assertEqual(
                {row["id"] for row in preview.data["transactions"]},
                expected_ids,
            )
            self.assertEqual(preview.data["transaction_count"], 3)
            self.assertEqual(preview.data["net_amount"], "1250.00")

        for user in (self.owner, self.manager):
            self.client.force_authenticate(user=user)
            summary = self.client.get(
                self.settlement_unsettled_summary_url(self.club),
                {"collected_by": self.staff.id},
            )
            self.assertEqual(summary.status_code, status.HTTP_200_OK)
            self.assertEqual(summary.data["results"][0]["transaction_count"], 3)
            self.assertEqual(summary.data["results"][0]["net_amount"], "1250.00")

        self.client.force_authenticate(user=self.owner)
        created = self.client.post(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff.id},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(
                Settlement.objects.get(pk=created.data["id"]).lines.values_list(
                    "transaction_id",
                    flat=True,
                )
            ),
            expected_ids,
        )
        self.assertEqual(created.data["transaction_count"], 3)
        self.assertEqual(created.data["total_amount"], "1250.00")

    def test_court_scope_applies_only_when_explicit_and_authorized(self):
        other_booking = self.create_booking(
            self.other_court,
            customer_name="Other Court Custody",
        )
        other_court_transaction = self.create_transaction(
            other_booking,
            amount=Decimal("75.00"),
            created_by=self.staff,
            payment_reference="CUSTODY-OTHER-COURT",
        )
        self.client.force_authenticate(user=self.owner)

        all_courts = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff.id},
        )
        selected_court = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff.id, "court": self.court.id},
        )

        self.assertEqual(all_courts.status_code, status.HTTP_200_OK)
        self.assertEqual(selected_court.status_code, status.HTTP_200_OK)
        # Financial custody scope is Club + Collector, not restricted by Court
        self.assertIn(
            other_court_transaction.id,
            {row["id"] for row in all_courts.data["transactions"]},
        )
        self.assertIn(
            other_court_transaction.id,
            {row["id"] for row in selected_court.data["transactions"]},
        )
        self.assertEqual(
            {row["id"] for row in all_courts.data["transactions"]},
            {row["id"] for row in selected_court.data["transactions"]},
        )

    def test_canonical_candidate_function_has_no_period_or_method_parameters(self):
        import inspect

        from apps.settlements.services import get_unsettled_transactions_queryset

        self.assertEqual(
            set(inspect.signature(get_unsettled_transactions_queryset).parameters),
            {"club", "collector", "lock"},
        )


class SettlementNegativeCustodyFlowTests(SettlementAPITestCase):
    def setUp(self):
        self.owner = self.create_user("negative-custody-owner")
        self.staff = self.create_user("negative-custody-staff")
        self.club = self.create_club("Negative Custody Club", slug="negative-custody")
        self.court = self.create_court(
            self.club,
            "Negative Custody Court",
            cancellation_refund_notice_days=0,
            minimum_deposit=Decimal("50.00"),
        )
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_valid_settlement_then_full_refund_reaches_negative_current_custody(self):
        booking = self.create_booking(
            self.court,
            customer_name="Negative Custody Customer",
            start_time=timezone.now() + timedelta(days=10),
            end_time=timezone.now() + timedelta(days=10, hours=1),
            total_price=Decimal("500.00"),
            status=Booking.Status.CONFIRMED,
        )
        payment = self.create_transaction(
            booking,
            amount=Decimal("500.00"),
            created_by=self.staff,
            payment_reference="NEGATIVE-CUSTODY-PAYMENT",
        )
        self.client.force_authenticate(user=self.owner)
        settlement_response = self.client.post(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff.id},
            format="json",
        )
        self.assertEqual(settlement_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            SettlementTransaction.objects.filter(transaction=payment).exists()
        )

        self.client.force_authenticate(user=self.staff)
        cancellation = self.client.post(
            self.booking_action_url(self.club, booking, "cancel"),
            {
                "reason": "Customer cancelled within full-refund policy",
                "refund_payment_method": Transaction.PaymentMethod.CASH,
            },
            format="json",
        )
        self.assertEqual(cancellation.status_code, status.HTTP_200_OK)
        refund = Transaction.objects.get(
            booking=booking,
            transaction_type=Transaction.Type.REFUND,
        )
        self.assertEqual(refund.amount, Decimal("-500.00"))
        self.assertEqual(refund.created_by, self.staff)
        self.assertFalse(hasattr(refund, "settlement_line"))

        preview = self.client.get(self.settlement_preview_url(self.club))
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(preview.data["transaction_count"], 1)
        self.assertEqual(preview.data["net_amount"], "-500.00")
        self.assertEqual(
            {row["id"] for row in preview.data["transactions"]},
            {refund.id},
        )


class SettlementMarkSettledTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("mark-admin")
        self.club = self.create_club("Mark Club", slug="settlement-mark")
        self.other_club = self.create_club("Other Mark Club", slug="other-mark")
        self.court = self.create_court(self.club, "Mark Court")
        self.other_court = self.create_court(self.other_club, "Other Mark Court")
        self.pending = self.create_settlement(self.club, court=self.court)
        self.settled = self.create_settlement(
            self.club,
            court=self.court,
            period_start=self.time_at(15),
            period_end=self.time_at(16),
            status=Settlement.Status.SETTLED,
            settled_by=self.platform_admin,
            settled_at=timezone.now(),
        )
        self.other_settlement = self.create_settlement(
            self.other_club,
            court=self.other_court,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_pending_settlement_can_be_marked_settled(self):
        response = self.client.post(
            self.settlement_mark_settled_url(self.club, self.pending),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Settlement.Status.SETTLED)
        self.assertEqual(self.pending.settled_by, self.platform_admin)
        self.assertIsNotNone(self.pending.settled_at)

    def test_already_settled_settlement_cannot_be_marked_again(self):
        response = self.client.post(
            self.settlement_mark_settled_url(self.club, self.settled),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "SETTLEMENT_ALREADY_SETTLED")
        self.assertEqual(
            response.data["message"],
            "This settlement is already settled.",
        )
        self.assertNotIn("settlement", response.data)

    def test_inaccessible_settlement_cannot_be_marked_settled(self):
        response = self.client.post(
            self.settlement_mark_settled_url(self.club, self.other_settlement),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SettlementImmutabilityFilterPatternTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("filter-admin")
        self.staff = self.create_user("filter-staff")
        self.creator = self.create_user("filter-creator")
        self.settler = self.create_user("filter-settler")
        self.club = self.create_club("Filter Club", slug="settlement-filter")
        self.other_club = self.create_club("Other Filter Club", slug="other-filter")
        self.court = self.create_court(self.club, "Filter Court")
        self.same_club_other_court = self.create_court(self.club, "Filter Other Court")
        self.other_court = self.create_court(self.other_club, "Other Filter Court")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.pending = self.create_settlement(
            self.club,
            court=self.court,
            collected_by=self.staff,
            created_by=self.creator,
            period_start=self.time_at(10),
            period_end=self.time_at(14),
        )
        self.settled = self.create_settlement(
            self.club,
            court=self.same_club_other_court,
            collected_by=self.creator,
            status=Settlement.Status.SETTLED,
            settled_by=self.settler,
            settled_at=self.time_at(18),
            period_start=self.time_at(15),
            period_end=self.time_at(17),
        )
        self.other_settlement = self.create_settlement(
            self.other_club,
            court=self.other_court,
            collected_by=self.other_staff if hasattr(self, "other_staff") else None,
            period_start=self.time_at(10),
            period_end=self.time_at(14),
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_patch_put_and_delete_are_not_allowed(self):
        detail_url = self.settlement_detail_url(self.club, self.pending)

        patch_response = self.client.patch(detail_url, {"notes": "x"}, format="json")
        put_response = self.client.put(detail_url, {"notes": "x"}, format="json")
        delete_response = self.client.delete(detail_url)

        self.assertEqual(patch_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(put_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(
            delete_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_filter_by_status(self):
        response = self.client.get(
            self.settlement_list_url(self.club),
            {"status": Settlement.Status.SETTLED},
        )

        self.assertEqual(self.list_ids(response), {self.settled.id})

    def test_filter_by_court(self):
        response = self.client.get(
            self.settlement_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(self.list_ids(response), {self.pending.id})

    def test_filter_by_period_from_and_period_to(self):
        period_from_response = self.client.get(
            self.settlement_list_url(self.club),
            {
                "period_from": self.time_at(
                    14,
                ).isoformat()
            },
        )
        period_to_response = self.client.get(
            self.settlement_list_url(self.club),
            {"period_to": self.time_at(15).isoformat()},
        )

        self.assertEqual(self.list_ids(period_from_response), {self.settled.id})
        self.assertEqual(self.list_ids(period_to_response), {self.pending.id})

    def test_filter_by_created_by_and_settled_by(self):
        created_response = self.client.get(
            self.settlement_list_url(self.club),
            {"created_by": self.creator.id},
        )
        settled_response = self.client.get(
            self.settlement_list_url(self.club),
            {"settled_by": self.settler.id},
        )

        self.assertEqual(self.list_ids(created_response), {self.pending.id})
        self.assertEqual(self.list_ids(settled_response), {self.settled.id})

    def test_filter_by_collected_by(self):
        response = self.client.get(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff.id},
        )

        self.assertEqual(self.list_ids(response), {self.pending.id})

    def test_settlement_detail_includes_collected_by(self):
        response = self.client.get(self.settlement_detail_url(self.club, self.pending))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["collected_by"], self.staff.id)
        self.assertEqual(response.data["collected_by_name"], self.staff.username)
        self.assertEqual(response.data["court_name"], self.court.name)
        self.assertEqual(response.data["settled_by_name"], "")

    def test_settled_by_name_uses_full_name_then_username(self):
        self.settler.first_name = "Mohamed"
        self.settler.last_name = "Ahmed"
        self.settler.save(update_fields=["first_name", "last_name"])

        list_response = self.client.get(
            self.settlement_list_url(self.club),
            {"status": Settlement.Status.SETTLED},
        )
        detail_response = self.client.get(
            self.settlement_detail_url(self.club, self.settled)
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            list_response.data["results"][0]["settled_by_name"],
            "Mohamed Ahmed",
        )
        self.assertEqual(detail_response.data["settled_by_name"], "Mohamed Ahmed")

        self.settler.first_name = ""
        self.settler.last_name = ""
        self.settler.save(update_fields=["first_name", "last_name"])
        username_response = self.client.get(
            self.settlement_detail_url(self.club, self.settled)
        )
        self.assertEqual(
            username_response.data["settled_by_name"],
            self.settler.username,
        )

    def test_filters_respect_club_access_and_ignore_club_query_param(self):
        response = self.client.get(
            self.settlement_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(self.list_ids(response), {self.pending.id, self.settled.id})
        self.assertNotIn(self.other_settlement.id, self.list_ids(response))

    def test_staff_filter_request_returns_own_settlements(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.pending.id})

    def test_settlement_route_resolves_to_viewset(self):
        match = resolve("/api/v1/clubs/example-club/settlements/")

        self.assertIs(match.func.cls, SettlementViewSet)

    def test_settlement_viewset_uses_django_filter_backend(self):
        self.assertEqual(SettlementViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(SettlementViewSet.filterset_class, SettlementFilter)

    def test_settlement_viewset_does_not_manually_parse_filter_query_params(self):
        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "settlements" / "views.py").read_text()

        self.assertNotIn("request.query_params.get", view_source)
        self.assertNotIn("parse_date", view_source)
        self.assertNotIn("parse_datetime", view_source)

    def test_settlement_filter_does_not_contain_access_logic(self):
        repo_root = Path(__file__).resolve().parents[2]
        filter_source = (repo_root / "apps" / "settlements" / "filters.py").read_text()

        self.assertNotIn("ClubMembership", filter_source)
        self.assertNotIn("ClubAccessContext", filter_source)
        self.assertNotIn("club_slug", filter_source)

    def test_settlement_viewset_uses_spine_v2_mixin(self):
        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "settlements" / "views.py").read_text()

        self.assertIn("SlotyScopedResourceMixin", view_source)
        self.assertIn("SlotyBasePermission", view_source)
        self.assertNotIn("ClubScopedViewMixin", view_source)
        self.assertNotIn("Settlement.objects.filter", view_source)
        self.assertNotIn("scoped_settlements_queryset", view_source)

    def test_no_forbidden_role_or_permission_files_were_introduced(self):
        repo_root = Path(__file__).resolve().parents[2]
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)
        self.assertNotIn(
            "CourtStaffAssignment",
            (repo_root / "apps" / "courts" / "models.py").read_text(),
        )
        self.assertFalse(
            (repo_root / "apps" / "settlements" / "permissions.py").exists()
        )
        self.assertFalse(
            (repo_root / "apps" / "transactions" / "permissions.py").exists()
        )


class SettlementSeedSchemaTests(SettlementAPITestCase):
    def test_seed_demo_data_creates_settlement_examples_idempotently(self):
        call_command("seed_demo_data", verbosity=0)
        counts = {
            "settlements": Settlement.objects.count(),
            "lines": SettlementTransaction.objects.count(),
            "unsettled": Transaction.objects.filter(
                settlement_line__isnull=True
            ).count(),
        }

        call_command("seed_demo_data", verbosity=0)

        self.assertEqual(Settlement.objects.count(), counts["settlements"])
        self.assertEqual(SettlementTransaction.objects.count(), counts["lines"])
        self.assertEqual(
            Transaction.objects.filter(settlement_line__isnull=True).count(),
            counts["unsettled"],
        )
        self.assertTrue(
            Settlement.objects.filter(
                club__slug="barcelona-fc",
                status=Settlement.Status.PENDING,
            ).exists()
        )
        self.assertTrue(
            Transaction.objects.filter(
                club__slug="barcelona-fc",
                settlement_line__isnull=True,
                payment_reference__in=["A-UNSETTLED-001", "A-UNSETTLED-002"],
            ).exists()
        )

    def test_schema_and_docs_return_200_and_include_settlement_endpoints(self):
        schema_response = self.client.get(reverse("schema"))
        docs_response = self.client.get(reverse("swagger-ui"))
        schema = schema_response.content.decode()
        schema_doc = yaml.safe_load(schema)

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "/api/v1/clubs/{club_slug}/settlements/unsettled-summary/",
            schema,
        )
        self.assertIn("booking_customer_name", schema)
        self.assertIn("court_name", schema)
        self.assertIn("settled_by_name", schema)
        components = schema_doc["components"]["schemas"]
        preview_fields = components["SettlementPreviewResponse"]["properties"]
        summary_fields = components["SettlementUnsettledSummaryRow"]["properties"]
        self.assertIn("net_amount", preview_fields)
        self.assertIn("net_amount", summary_fields)
        self.assertIn("booking_payments", summary_fields)
        self.assertIn("booking_refunds", summary_fields)


class SettlementQueryScalingTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("settlement-query-admin")
        self.club = self.create_club("Settlement Query Club", slug="settlement-query")
        self.court = self.create_court(self.club, "Settlement Query Court")
        self.client.force_authenticate(user=self.platform_admin)

    def add_preview_candidate(self, index):
        booking = self.create_booking(
            self.court,
            customer_name=f"Preview Customer {index}",
            customer_phone=f"+2010000090{index:02d}",
            start_time=self.time_at(8 + (index % 10)),
            end_time=self.time_at(9 + (index % 10)),
        )
        return self.create_transaction(
            booking,
            created_by=self.platform_admin,
            payment_reference=f"PREVIEW-Q-{index}",
        )

    def test_settlement_list_query_count_does_not_grow_with_rows(self):
        self.create_settlement(self.club, court=self.court)

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(self.settlement_list_url(self.club))

        for index in range(9):
            self.create_settlement(
                self.club,
                court=self.court,
                notes=f"extra-{index}",
            )

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["count"], 1)
        self.assertEqual(second_response.data["count"], 10)
        self.assertEqual(len(first), len(second))

    def test_settlement_preview_query_count_does_not_grow_with_candidates(self):
        self.add_preview_candidate(0)

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(
                self.settlement_preview_url(self.club),
                {"collected_by": self.platform_admin.id},
            )

        for index in range(1, 30):
            self.add_preview_candidate(index)

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(
                self.settlement_preview_url(self.club),
                {"collected_by": self.platform_admin.id},
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["transaction_count"], 1)
        self.assertEqual(second_response.data["transaction_count"], 30)
        self.assertEqual(len(first), len(second))

    def test_settlement_detail_query_count_does_not_grow_with_line_customer_fields(
        self,
    ):
        first_booking = self.create_booking(
            self.court,
            customer_name="First Customer",
            customer_phone="+201000009101",
        )
        first_transaction = self.create_transaction(
            first_booking,
            created_by=self.platform_admin,
            payment_reference="DETAIL-Q-1",
        )
        first_settlement = self.create_settlement(
            self.club,
            court=self.court,
            collected_by=self.platform_admin,
            status=Settlement.Status.SETTLED,
        )
        SettlementTransaction.objects.create(
            settlement=first_settlement,
            transaction=first_transaction,
            amount=first_transaction.amount,
        )

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(
                self.settlement_detail_url(self.club, first_settlement)
            )

        many_settlement = self.create_settlement(
            self.club,
            court=self.court,
            collected_by=self.platform_admin,
            status=Settlement.Status.SETTLED,
            notes="many-lines",
        )
        for index in range(12):
            booking = self.create_booking(
                self.court,
                customer_name=f"Customer {index}",
                customer_phone=f"+2010000092{index:02d}",
                start_time=self.time_at(8 + (index % 10)),
                end_time=self.time_at(9 + (index % 10)),
            )
            transaction_obj = self.create_transaction(
                booking,
                created_by=self.platform_admin,
                payment_reference=f"DETAIL-Q-M-{index}",
            )
            SettlementTransaction.objects.create(
                settlement=many_settlement,
                transaction=transaction_obj,
                amount=transaction_obj.amount,
            )

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(
                self.settlement_detail_url(self.club, many_settlement)
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first_response.data["lines"]), 1)
        self.assertEqual(len(second_response.data["lines"]), 12)
        self.assertEqual(
            first_response.data["lines"][0]["booking_customer_name"],
            "First Customer",
        )
        self.assertEqual(len(first), len(second))


class SettlementUnsettledSummaryTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("summary-admin")
        self.owner = self.create_user(
            "summary-owner",
            first_name="Owner",
            last_name="One",
        )
        self.manager = self.create_user(
            "summary-manager",
            first_name="Manager",
            last_name="Settle",
        )
        self.manager_denied = self.create_user("summary-manager-denied")
        self.staff = self.create_user(
            "summary-staff",
            first_name="Mohamed",
            last_name="Ahmed",
        )
        self.other_staff = self.create_user(
            "summary-staff-two",
            first_name="Ahmed",
            last_name="Ali",
        )
        self.club = self.create_club("Summary Club", slug="unsettled-summary")
        self.other_club = self.create_club("Other Summary Club", slug="other-unsettled")
        self.court = self.create_court(self.club, "Summary Court")
        self.other_court = self.create_court(self.other_club, "Other Summary Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        self.create_membership(
            self.manager_denied,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=False,
        )
        self.create_membership(
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
        self.booking = self.create_booking(
            self.court,
            customer_name="Summary Customer",
            customer_phone="+201012345678",
        )
        self.staff_cash = self.create_transaction(
            self.booking,
            amount=Decimal("800.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created=self.time_at(11),
            created_by=self.staff,
            payment_reference="SUM-CASH",
        )
        self.staff_wallet = self.create_transaction(
            self.booking,
            amount=Decimal("450.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            created=self.time_at(12),
            created_by=self.staff,
            payment_reference="SUM-WALLET",
        )
        self.staff_refund = self.create_transaction(
            self.booking,
            amount=Decimal("-100.00"),
            transaction_type=Transaction.Type.REFUND,
            created=self.time_at(13),
            created_by=self.staff,
            payment_reference="SUM-REFUND",
        )
        self.other_staff_tx = self.create_transaction(
            self.booking,
            amount=Decimal("200.00"),
            created=self.time_at(14),
            created_by=self.other_staff,
            payment_reference="SUM-OTHER-STAFF",
        )
        self.owner_tx = self.create_transaction(
            self.booking,
            amount=Decimal("500.00"),
            created=self.time_at(15),
            created_by=self.owner,
            payment_reference="SUM-OWNER",
        )
        other_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000009999",
        )
        self.create_transaction(
            other_booking,
            amount=Decimal("999.00"),
            created_by=self.owner,
            payment_reference="SUM-OTHER-CLUB",
        )

    def test_owner_sees_grouped_unsettled_collectors_with_signed_totals(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.settlement_unsettled_summary_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row["collected_by"]: row for row in response.data["results"]}
        self.assertEqual(
            set(by_id),
            {self.staff.id, self.other_staff.id, self.owner.id},
        )
        staff_row = by_id[self.staff.id]
        self.assertEqual(staff_row["collected_by_name"], "Mohamed Ahmed")
        self.assertEqual(staff_row["transaction_count"], 3)
        self.assertEqual(staff_row["booking_payments"], "1250.00")
        self.assertEqual(staff_row["booking_refunds"], "-100.00")
        self.assertEqual(staff_row["total_amount"], "1150.00")
        self.assertEqual(staff_row["totals_by_payment_method"]["CASH"], "700.00")
        self.assertEqual(
            staff_row["totals_by_payment_method"]["DIGITAL_WALLET"],
            "450.00",
        )
        self.assertEqual(staff_row["totals_by_payment_method"]["BANK_TRANSFER"], "0.00")
        self.assertEqual(staff_row["totals_by_payment_method"]["OTHER"], "0.00")
        self.assertFalse(staff_row["is_self"])
        self.assertTrue(staff_row["can_approve"])
        period_start = staff_row["period_start"]
        if isinstance(period_start, str):
            period_start = parse_datetime(period_start)
        self.assertEqual(period_start, self.staff_cash.created)
        owner_row = by_id[self.owner.id]
        self.assertTrue(owner_row["is_self"])
        self.assertTrue(owner_row["can_approve"])

    def test_old_unsettled_transaction_remains_visible_days_later(self):
        old_created = timezone.now() - timedelta(days=4)
        Transaction.objects.filter(pk=self.staff_cash.pk).update(created=old_created)
        self.staff_cash.refresh_from_db()
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.settlement_unsettled_summary_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        staff_row = next(
            row
            for row in response.data["results"]
            if row["collected_by"] == self.staff.id
        )
        self.assertEqual(staff_row["total_amount"], "1150.00")
        self.assertEqual(staff_row["transaction_count"], 3)

    def test_zero_net_collector_remains_visible_but_zero_candidate_collector_does_not(
        self,
    ):
        zero_net_collector = self.create_user("summary-zero-net")
        no_candidates_collector = self.create_user("summary-zero-candidates")
        for collector in (zero_net_collector, no_candidates_collector):
            self.create_membership(
                collector,
                self.club,
                ClubMembership.Role.STAFF,
                court=self.court,
            )
        self.create_transaction(
            self.booking,
            amount=Decimal("100.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=zero_net_collector,
            payment_reference="SUM-ZERO-PAYMENT",
        )
        self.create_transaction(
            self.booking,
            transaction_type=Transaction.Type.REFUND,
            amount=Decimal("-100.00"),
            payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
            created_by=zero_net_collector,
            payment_reference="SUM-ZERO-REFUND",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.settlement_unsettled_summary_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row["collected_by"]: row for row in response.data["results"]}
        self.assertIn(zero_net_collector.id, by_id)
        self.assertEqual(by_id[zero_net_collector.id]["transaction_count"], 2)
        self.assertEqual(by_id[zero_net_collector.id]["net_amount"], "0.00")
        self.assertEqual(
            by_id[zero_net_collector.id]["totals_by_payment_method"]["CASH"],
            "100.00",
        )
        self.assertEqual(
            by_id[zero_net_collector.id]["totals_by_payment_method"]["DIGITAL_WALLET"],
            "-100.00",
        )
        self.assertNotIn(no_candidates_collector.id, by_id)

    def test_manager_cannot_see_owner_money_and_cannot_approve_self(self):
        self.create_transaction(
            self.booking,
            amount=Decimal("75.00"),
            created=self.time_at(16),
            created_by=self.manager,
            payment_reference="SUM-MANAGER-SELF",
        )
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.settlement_unsettled_summary_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row["collected_by"]: row for row in response.data["results"]}
        self.assertNotIn(self.owner.id, by_id)
        self.assertIn(self.staff.id, by_id)
        self.assertTrue(by_id[self.staff.id]["can_approve"])
        self.assertTrue(by_id[self.manager.id]["is_self"])
        self.assertFalse(by_id[self.manager.id]["can_approve"])

    def test_staff_and_manager_without_flag_cannot_access_summary(self):
        self.client.force_authenticate(user=self.staff)
        staff_response = self.client.get(
            self.settlement_unsettled_summary_url(self.club)
        )
        self.client.force_authenticate(user=self.manager_denied)
        denied_response = self.client.get(
            self.settlement_unsettled_summary_url(self.club)
        )

        self.assertEqual(staff_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(denied_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_collected_by_filter_validates_access_and_club(self):
        self.client.force_authenticate(user=self.owner)
        filtered = self.client.get(
            self.settlement_unsettled_summary_url(self.club),
            {"collected_by": self.staff.id},
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(len(filtered.data["results"]), 1)
        self.assertEqual(filtered.data["results"][0]["collected_by"], self.staff.id)

        outsider = self.create_user("summary-outsider")
        self.create_membership(
            outsider,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )
        other_club = self.client.get(
            self.settlement_unsettled_summary_url(self.club),
            {"collected_by": outsider.id},
        )
        self.assertEqual(other_club.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(user=self.manager)
        owner_money = self.client.get(
            self.settlement_unsettled_summary_url(self.club),
            {"collected_by": self.owner.id},
        )
        self.assertEqual(owner_money.status_code, status.HTTP_403_FORBIDDEN)

    def test_does_not_create_settlements_or_duplicate_mutation_routes(self):
        before = Settlement.objects.count()
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.settlement_unsettled_summary_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Settlement.objects.count(), before)
        repo_root = Path(__file__).resolve().parents[2]
        urls_source = (repo_root / "apps" / "settlements" / "urls.py").read_text()
        self.assertIn("unsettled-summary", urls_source)
        self.assertNotIn("receive-money", urls_source)
        self.assertNotIn("approve-custody", urls_source)


class SettlementUnsettledSummaryQueryScalingTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("summary-query-admin")
        self.club = self.create_club("Summary Query Club", slug="summary-query")
        self.court = self.create_court(self.club, "Summary Query Court")
        self.client.force_authenticate(user=self.platform_admin)

    def add_collector(self, index):
        user = self.create_user(
            f"summary-collector-{index}",
            first_name="Collector",
            last_name=str(index),
        )
        self.create_membership(
            user,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        booking = self.create_booking(
            self.court,
            customer_phone=f"+2010000081{index:02d}",
            start_time=self.time_at(8 + (index % 10)),
            end_time=self.time_at(9 + (index % 10)),
        )
        self.create_transaction(
            booking,
            created_by=user,
            payment_reference=f"SUM-SCALE-{index}",
        )
        return user

    def test_query_count_does_not_grow_per_collector(self):
        self.add_collector(0)

        with CaptureQueriesContext(connection) as first:
            first_response = self.client.get(
                self.settlement_unsettled_summary_url(self.club)
            )

        for index in range(1, 30):
            self.add_collector(index)

        with CaptureQueriesContext(connection) as second:
            second_response = self.client.get(
                self.settlement_unsettled_summary_url(self.club)
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first_response.data["results"]), 1)
        self.assertEqual(len(second_response.data["results"]), 30)
        self.assertLessEqual(len(second) - len(first), 2)
        self.assertLess(len(second), len(first) + 30)


class FinancialCustodyDomainInvariantsTests(SettlementAPITestCase):
    """
    Comprehensive tests for the Financial Custody Domain Spine:
    Financial Scope = Club + optional Collector, NOT Court.
    """

    def setUp(self):
        self.club = self.create_club("Financial Spine Club", slug="financial-spine")
        self.other_club = self.create_club(
            "Other Financial Club", slug="other-financial"
        )

        self.court_a = self.create_court(self.club, "Court A")
        self.court_b = self.create_court(self.club, "Court B")
        self.court_c = self.create_court(self.club, "Court C")
        self.other_club_court = self.create_court(self.other_club, "Other Club Court")

        self.owner = self.create_user("spine-owner")
        self.manager = self.create_user("spine-manager")
        self.staff_a = self.create_user("spine-staff-a")
        self.staff_b = self.create_user("spine-staff-b")
        self.other_club_staff = self.create_user("spine-other-staff")

        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        # Staff A is assigned to Court A only
        self.create_membership(
            self.staff_a,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court_a,
        )
        # Staff B is assigned to Court B only
        self.create_membership(
            self.staff_b,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court_b,
        )
        self.create_membership(
            self.other_club_staff,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.other_club_court,
        )

        self.booking_a = self.create_booking(
            self.court_a, customer_name="Booking Court A"
        )
        self.booking_b = self.create_booking(
            self.court_b, customer_name="Booking Court B"
        )
        self.booking_c = self.create_booking(
            self.court_c, customer_name="Booking Court C"
        )
        self.other_club_booking = self.create_booking(
            self.other_club_court, customer_name="Other Club Booking"
        )

        # Staff A collects transactions across Court A, Court B, and Court C
        self.t1 = self.create_transaction(
            self.booking_a,
            amount=Decimal("100.00"),
            created_by=self.staff_a,
            payment_reference="SPINE-T1",
        )
        self.t2 = self.create_transaction(
            self.booking_b,
            amount=Decimal("150.00"),
            created_by=self.staff_a,
            payment_reference="SPINE-T2",
        )
        self.t3 = self.create_transaction(
            self.booking_c,
            amount=Decimal("200.00"),
            created_by=self.staff_a,
            payment_reference="SPINE-T3",
        )

        # Staff B collects on Court B
        self.t_staff_b = self.create_transaction(
            self.booking_b,
            amount=Decimal("300.00"),
            created_by=self.staff_b,
            payment_reference="SPINE-STAFF-B",
        )

        # Other club transaction
        self.t_other_club = self.create_transaction(
            self.other_club_booking,
            amount=Decimal("500.00"),
            created_by=self.other_club_staff,
            payment_reference="SPINE-OTHER-CLUB",
        )

    def test_1_staff_self_preview_includes_transactions_across_all_courts(self):
        """
        Staff self-preview must include all transactions collected by Staff
        in this Club, across all courts.
        """
        self.client.force_authenticate(user=self.staff_a)
        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["total_amount"], "450.00")
        self.assertEqual(response.data["net_amount"], "450.00")
        transaction_ids = {tx["id"] for tx in response.data["transactions"]}
        self.assertEqual(transaction_ids, {self.t1.id, self.t2.id, self.t3.id})

    def test_2_owner_preview_matches_staff_self_preview_exactly(self):
        """
        Owner previewing Staff A must resolve to the exact same transaction set
        and net_amount as Staff A self-preview.
        """
        self.client.force_authenticate(user=self.staff_a)
        staff_preview = self.client.get(self.settlement_preview_url(self.club))

        self.client.force_authenticate(user=self.owner)
        owner_preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )

        self.assertEqual(staff_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(
            staff_preview.data["transaction_count"],
            owner_preview.data["transaction_count"],
        )
        self.assertEqual(
            staff_preview.data["net_amount"], owner_preview.data["net_amount"]
        )
        self.assertEqual(
            {tx["id"] for tx in staff_preview.data["transactions"]},
            {tx["id"] for tx in owner_preview.data["transactions"]},
        )

    def test_3_manager_preview_matches_staff_self_preview_exactly(self):
        """
        Manager previewing Staff A must resolve to the exact same transaction
        set and net_amount as Staff A self-preview.
        """
        self.client.force_authenticate(user=self.staff_a)
        staff_preview = self.client.get(self.settlement_preview_url(self.club))

        self.client.force_authenticate(user=self.manager)
        manager_preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )

        self.assertEqual(staff_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(manager_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(
            staff_preview.data["transaction_count"],
            manager_preview.data["transaction_count"],
        )
        self.assertEqual(
            staff_preview.data["net_amount"], manager_preview.data["net_amount"]
        )
        self.assertEqual(
            {tx["id"] for tx in staff_preview.data["transactions"]},
            {tx["id"] for tx in manager_preview.data["transactions"]},
        )

    def test_4_collector_isolation(self):
        """Staff A preview must not receive transactions collected by Staff B."""
        self.client.force_authenticate(user=self.staff_a)
        preview_a = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(preview_a.status_code, status.HTTP_200_OK)
        ids_a = {tx["id"] for tx in preview_a.data["transactions"]}
        self.assertNotIn(self.t_staff_b.id, ids_a)

        self.client.force_authenticate(user=self.staff_b)
        preview_b = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(preview_b.status_code, status.HTTP_200_OK)
        ids_b = {tx["id"] for tx in preview_b.data["transactions"]}
        self.assertEqual(ids_b, {self.t_staff_b.id})

    def test_5_club_isolation(self):
        """Transactions from another Club must never appear in Current Custody."""
        self.client.force_authenticate(user=self.owner)
        preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        ids = {tx["id"] for tx in preview.data["transactions"]}
        self.assertNotIn(self.t_other_club.id, ids)

        # Cross-club preview via get_unsettled_transactions_queryset
        from apps.settlements.services import get_unsettled_transactions_queryset

        club_txs = set(
            get_unsettled_transactions_queryset(club=self.club).values_list(
                "id", flat=True
            )
        )
        self.assertNotIn(self.t_other_club.id, club_txs)

    def test_6_no_collector_includes_all_eligible_unsettled_transactions_in_club(self):
        """
        collector=None must include all eligible unsettled transactions in the
        scoped Club across all courts.
        """
        from apps.settlements.services import (
            build_custody,
            get_unsettled_transactions_queryset,
        )

        all_club_unsettled = set(
            get_unsettled_transactions_queryset(
                club=self.club, collector=None
            ).values_list("id", flat=True)
        )
        self.assertEqual(
            all_club_unsettled, {self.t1.id, self.t2.id, self.t3.id, self.t_staff_b.id}
        )

        custody_snapshot = build_custody(club=self.club, collector=None)
        self.assertEqual(custody_snapshot["transaction_count"], 4)
        self.assertEqual(custody_snapshot["net_amount"], Decimal("750.00"))

    def test_7_settlement_mutation_consumes_same_candidate_source_as_preview(self):
        """
        Settlement create consumes the exact same eligible transactions as
        preview.
        """
        self.client.force_authenticate(user=self.owner)
        preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )
        preview_ids = {tx["id"] for tx in preview.data["transactions"]}
        self.assertEqual(preview_ids, {self.t1.id, self.t2.id, self.t3.id})

        create_response = self.client.post(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff_a.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        settlement_obj = Settlement.objects.get(pk=create_response.data["id"])
        settled_ids = set(settlement_obj.lines.values_list("transaction_id", flat=True))
        self.assertEqual(settled_ids, preview_ids)
        self.assertEqual(settlement_obj.total_amount, Decimal("450.00"))

    def test_8_operational_transaction_authorization_remains_court_restricted(self):
        """
        Staff A cannot access cross-court transactions via operational
        Transaction APIs.
        """
        self.client.force_authenticate(user=self.staff_a)
        transactions_url = reverse(
            "club-transaction-list", kwargs={"club_slug": self.club.slug}
        )
        response = self.client.get(transactions_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Staff A is assigned to Court A only, so operational list must only
        # show Court A transactions
        accessible_tx_ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.t1.id, accessible_tx_ids)
        self.assertNotIn(self.t2.id, accessible_tx_ids)
        self.assertNotIn(self.t3.id, accessible_tx_ids)
        self.assertNotIn(self.t_staff_b.id, accessible_tx_ids)

    def test_9_no_court_restriction_in_custody(self):
        """
        Current custody for a collector contains transactions across multiple
        courts even if court is provided.
        """
        self.client.force_authenticate(user=self.owner)
        preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id, "court": self.court_a.id},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        # Transactions on Court B and Court C must not be filtered out of custody
        tx_ids = {tx["id"] for tx in preview.data["transactions"]}
        self.assertEqual(tx_ids, {self.t1.id, self.t2.id, self.t3.id})
        self.assertEqual(preview.data["transaction_count"], 3)
        self.assertEqual(preview.data["total_amount"], "450.00")

    def test_10_preview_is_strictly_side_effect_free(self):
        """
        Preview must not create Settlements, SettlementTransactions, or mutate
        financial state.
        """
        settlements_count_before = Settlement.objects.count()
        lines_count_before = SettlementTransaction.objects.count()
        booking_statuses_before = {b.id: b.status for b in Booking.objects.all()}

        self.client.force_authenticate(user=self.staff_a)
        response = self.client.get(self.settlement_preview_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Settlement.objects.count(), settlements_count_before)
        self.assertEqual(SettlementTransaction.objects.count(), lines_count_before)
        for booking in Booking.objects.all():
            self.assertEqual(booking.status, booking_statuses_before[booking.id])

        # Transactions remain unsettled
        self.assertFalse(
            Transaction.objects.filter(
                id__in=[self.t1.id, self.t2.id, self.t3.id],
                settlement_line__isnull=False,
            ).exists()
        )

    def test_11_repeated_preview_consistency(self):
        """
        Two successive previews with no intervening mutations must return
        identical transaction sets and net amounts.
        """
        self.client.force_authenticate(user=self.owner)
        preview_1 = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )
        preview_2 = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff_a.id},
        )

        self.assertEqual(preview_1.status_code, status.HTTP_200_OK)
        self.assertEqual(preview_2.status_code, status.HTTP_200_OK)
        self.assertEqual(
            preview_1.data["transaction_count"], preview_2.data["transaction_count"]
        )
        self.assertEqual(preview_1.data["net_amount"], preview_2.data["net_amount"])
        self.assertEqual(
            {tx["id"] for tx in preview_1.data["transactions"]},
            {tx["id"] for tx in preview_2.data["transactions"]},
        )
