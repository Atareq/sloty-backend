from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import resolve, reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.filters import SettlementFilter
from apps.settlements.models import Settlement, SettlementTransaction
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
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
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

    def settlement_payload(self, **extra_fields):
        data = {
            "period_start": self.time_at(10).isoformat(),
            "period_end": self.time_at(14).isoformat(),
        }
        data.update(extra_fields)
        return data

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


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
        self.club = self.create_club(
            "Access Club",
            slug="settlement-access",
            manager_can_settle_transactions=True,
        )
        self.other_club = self.create_club(
            "Other Access Club",
            slug="other-settlement-access",
        )
        self.court = self.create_court(self.club, "Access Court")
        self.other_court = self.create_court(self.other_club, "Other Access Court")
        self.booking = self.create_booking(self.court)
        self.create_transaction(self.booking)
        self.settlement = self.create_settlement(self.club, court=self.court)
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

    def test_anonymous_cannot_access_settlements(self):
        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_list_preview_create_and_settle(self):
        self.client.force_authenticate(user=self.platform_admin)

        list_response = self.client.get(self.settlement_list_url(self.club))
        preview_response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(),
        )
        create_response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(period_start=self.time_at(9).isoformat()),
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

    def test_manager_cannot_access_settlements_when_flag_disabled(self):
        self.club.manager_can_settle_transactions = False
        self.club.save(update_fields=["manager_can_settle_transactions"])
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_access_settlements(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unrelated_club_member_cannot_access_selected_club_settlements(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SettlementPreviewCreateTests(SettlementAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("preview-admin")
        self.club = self.create_club("Preview Club", slug="settlement-preview")
        self.other_club = self.create_club("Other Preview Club", slug="other-preview")
        self.court = self.create_court(self.club, "Preview Court")
        self.same_club_other_court = self.create_court(self.club, "Preview Other Court")
        self.other_court = self.create_court(self.other_club, "Other Preview Court")
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
        self.first_transaction = self.create_transaction(
            self.booking,
            amount=Decimal("50.00"),
            created=self.time_at(11),
            payment_reference="PREVIEW-1",
        )
        self.second_transaction = self.create_transaction(
            self.second_booking,
            amount=Decimal("75.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-2",
        )
        self.other_court_transaction = self.create_transaction(
            self.other_court_booking,
            amount=Decimal("25.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-OTHER-COURT",
        )
        self.other_club_transaction = self.create_transaction(
            self.other_club_booking,
            amount=Decimal("90.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-OTHER-CLUB",
        )
        self.already_settled = self.create_transaction(
            self.booking,
            amount=Decimal("30.00"),
            created=self.time_at(13),
            payment_reference="PREVIEW-SETTLED",
        )
        self.voided_transaction = self.create_transaction(
            self.booking,
            amount=Decimal("40.00"),
            created=self.time_at(12),
            payment_reference="PREVIEW-VOIDED",
            is_voided=True,
            voided_by=self.platform_admin,
            voided_at=timezone.now(),
            void_reason="Wrong settlement amount",
        )
        settlement = self.create_settlement(
            self.club,
            court=self.court,
            total_amount=self.already_settled.amount,
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=self.already_settled,
            amount=self.already_settled.amount,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_preview_returns_correct_count_and_total(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 3)
        self.assertEqual(response.data["total_amount"], "150.00")

    def test_preview_excludes_voided_transactions(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(court=self.court.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 2)
        self.assertEqual(response.data["total_amount"], "125.00")

    def test_preview_respects_court_filter(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(court=self.court.id),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 2)
        self.assertEqual(response.data["total_amount"], "125.00")

    def test_preview_rejects_invalid_date_range(self):
        response = self.client.get(
            self.settlement_preview_url(self.club),
            self.settlement_payload(
                period_start=self.time_at(14).isoformat(),
                period_end=self.time_at(10).isoformat(),
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("period_end", response.data)

    def test_preview_respects_selected_club_scope(self):
        response = self.client.get(
            self.settlement_preview_url(self.other_club),
            self.settlement_payload(),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["transaction_count"], 1)
        self.assertEqual(response.data["total_amount"], "90.00")

    def test_create_settlement_from_unsettled_transactions(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(court=self.court.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        settlement = Settlement.objects.get(id=response.data["id"])
        self.assertEqual(settlement.status, Settlement.Status.PENDING)
        self.assertEqual(settlement.total_amount, Decimal("125.00"))
        self.assertEqual(settlement.transaction_count, 2)
        self.assertEqual(settlement.lines.count(), 2)

    def test_create_excludes_already_settled_transactions(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(court=self.court.id),
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
        self.assertNotIn(self.voided_transaction.id, transaction_ids)

    def test_creation_with_no_unsettled_transactions_returns_400(self):
        SettlementTransaction.objects.create(
            settlement=self.create_settlement(
                self.club,
                period_start=self.time_at(15),
                period_end=self.time_at(16),
                total_amount=self.first_transaction.amount,
            ),
            transaction=self.first_transaction,
            amount=self.first_transaction.amount,
        )
        SettlementTransaction.objects.create(
            settlement=self.create_settlement(
                self.club,
                period_start=self.time_at(16),
                period_end=self.time_at(17),
                total_amount=self.second_transaction.amount,
            ),
            transaction=self.second_transaction,
            amount=self.second_transaction.amount,
        )

        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(
                court=self.court.id,
                period_start=self.time_at(10).isoformat(),
                period_end=self.time_at(13).isoformat(),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("transactions", response.data)

    def test_court_from_another_club_is_rejected(self):
        response = self.client.post(
            self.settlement_list_url(self.club),
            self.settlement_payload(court=self.other_court.id),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("court", response.data)


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

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

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
            created_by=self.creator,
            period_start=self.time_at(10),
            period_end=self.time_at(14),
        )
        self.settled = self.create_settlement(
            self.club,
            court=self.same_club_other_court,
            status=Settlement.Status.SETTLED,
            settled_by=self.settler,
            settled_at=self.time_at(18),
            period_start=self.time_at(15),
            period_end=self.time_at(17),
        )
        self.other_settlement = self.create_settlement(
            self.other_club,
            court=self.other_court,
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

    def test_filters_respect_club_access_and_ignore_club_query_param(self):
        response = self.client.get(
            self.settlement_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(self.list_ids(response), {self.pending.id, self.settled.id})
        self.assertNotIn(self.other_settlement.id, self.list_ids(response))

    def test_staff_filter_request_is_forbidden(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.settlement_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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
                club__slug="demo-football-club",
                status=Settlement.Status.PENDING,
            ).exists()
        )
        self.assertTrue(
            Transaction.objects.filter(
                club__slug="demo-football-club",
                settlement_line__isnull=True,
                payment_reference__in=["A-UNSETTLED-001", "A-UNSETTLED-002"],
            ).exists()
        )

    def test_schema_and_docs_return_200_and_include_settlement_endpoints(self):
        schema_response = self.client.get(reverse("schema"))
        docs_response = self.client.get(reverse("swagger-ui"))

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "/api/v1/clubs/{club_slug}/settlements/",
            schema_response.content.decode(),
        )
