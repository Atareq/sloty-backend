from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.recurring.models import RecurringAgreement, RecurringDepositTransaction
from apps.settlements.models import SettlementRecurringDepositTransaction
from apps.transactions.models import Transaction
from apps.transactions.services import cancel_transaction, create_booking_transaction


class RecurringAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="recurring-admin") -> User:
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

    def create_working_hours(
        self,
        court: Court,
        *,
        weekday=2,
        opens_at=time(9, 0),
        closes_at=time(23, 0),
        is_closed=False,
        price=None,
    ) -> CourtWorkingHour:
        working_hour, _ = CourtWorkingHour.objects.update_or_create(
            court=court,
            weekday=weekday,
            defaults={
                "opens_at": opens_at if not is_closed else None,
                "closes_at": closes_at if not is_closed else None,
                "is_closed": is_closed,
            },
        )
        working_hour.pricing_periods.all().delete()
        if not is_closed:
            CourtWorkingHourPricePeriod.objects.create(
                working_hour=working_hour,
                starts_at=opens_at,
                ends_at=closes_at,
                price=price if price is not None else Decimal("300.00"),
            )
        return working_hour

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": Decimal("300.00"),
            "slot_duration_minutes": 60,
            "recurring_deposit_refund_notice_days": 1,
        }
        data.update(extra_fields)
        court = Court.objects.create(**data)
        # Configure every weekday so horizon generation always has pricing.
        for weekday in range(7):
            self.create_working_hours(court, weekday=weekday)
        return court

    def create_membership(
        self,
        user: User,
        club: Club,
        role: str,
        court: Court | None = None,
        **extra_fields,
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
            **extra_fields,
        )

    def next_weekday_date(self, weekday: int, *, days_ahead: int = 7):
        today = timezone.localdate()
        candidate = today + timedelta(days=days_ahead)
        while candidate.weekday() != weekday:
            candidate += timedelta(days=1)
        return candidate

    def agreement_list_url(self, club):
        return reverse(
            "club-recurring-agreement-list",
            kwargs={"club_slug": club.slug},
        )

    def agreement_detail_url(self, club, agreement):
        return reverse(
            "club-recurring-agreement-detail",
            kwargs={"club_slug": club.slug, "pk": agreement.pk},
        )

    def agreement_cancel_url(self, club, agreement):
        return reverse(
            "club-recurring-agreement-cancel",
            kwargs={"club_slug": club.slug, "pk": agreement.pk},
        )

    def agreement_preview_url(self, club, agreement):
        return reverse(
            "club-recurring-agreement-cancellation-preview",
            kwargs={"club_slug": club.slug, "pk": agreement.pk},
        )

    def agreement_refund_url(self, club, agreement):
        return reverse(
            "club-recurring-agreement-refund-deposit",
            kwargs={"club_slug": club.slug, "pk": agreement.pk},
        )

    def court_detail_url(self, club, court):
        return reverse(
            "club-court-detail",
            kwargs={"club_slug": club.slug, "pk": court.pk},
        )

    def settlement_preview_url(self, club):
        return reverse(
            "club-settlement-preview",
            kwargs={"club_slug": club.slug},
        )

    def settlement_list_url(self, club):
        return reverse(
            "club-settlement-list",
            kwargs={"club_slug": club.slug},
        )

    def agreement_payload(self, court, **extra_fields):
        weekday = 2
        data = {
            "court": court.id,
            "customer_name": "Recurring Customer",
            "customer_phone": "+201000000099",
            "weekday": weekday,
            "start_time": "20:00:00",
            "end_time": "21:00:00",
            "start_date": self.next_weekday_date(weekday).isoformat(),
            "payment_method": Transaction.PaymentMethod.CASH,
            "reference": "",
            "notes": "",
        }
        data.update(extra_fields)
        return data

    def post_agreement(self, club, court, **extra_fields):
        return self.client.post(
            self.agreement_list_url(club),
            self.agreement_payload(court, **extra_fields),
            format="json",
        )

    def assert_api_error(self, response, code):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], code)

    def make_access(self, user, club):
        request = type("Request", (), {"user": user})()
        return ClubAccessContext(request=request, club=club)


class RecurringPolicyTests(RecurringAPITestCase):
    def setUp(self):
        self.admin = self.create_platform_admin()
        self.owner = self.create_user("recurring-owner")
        self.manager = self.create_user("recurring-manager")
        self.staff = self.create_user("recurring-staff")
        self.club = self.create_club("Recurring Club", slug="recurring-club")
        self.court = self.create_court(self.club, "Recurring Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_court_refund_policy_maximum_is_30(self):
        self.court.recurring_deposit_refund_notice_days = 31
        with self.assertRaises(DjangoValidationError):
            self.court.full_clean()

    def test_null_court_policy_blocks_recurring_creation(self):
        self.court.recurring_deposit_refund_notice_days = None
        self.court.save(update_fields=["recurring_deposit_refund_notice_days"])
        self.client.force_authenticate(user=self.owner)
        response = self.post_agreement(self.club, self.court)
        self.assert_api_error(response, "RECURRING_POLICY_NOT_CONFIGURED")

    def test_staff_cannot_configure_policy(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(
            self.court_detail_url(self.club, self.court),
            {"recurring_deposit_refund_notice_days": 2},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_policy_is_snapshotted_on_agreement_creation(self):
        self.client.force_authenticate(user=self.owner)
        response = self.post_agreement(self.club, self.court)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        agreement = RecurringAgreement.objects.get(pk=response.data["id"])
        self.assertEqual(agreement.refund_notice_days_snapshot, 1)
        self.court.recurring_deposit_refund_notice_days = 5
        self.court.save(update_fields=["recurring_deposit_refund_notice_days"])
        agreement.refresh_from_db()
        self.assertEqual(agreement.refund_notice_days_snapshot, 1)


class RecurringCreateAccessTests(RecurringAPITestCase):
    def setUp(self):
        self.owner = self.create_user("create-owner")
        self.staff = self.create_user("create-staff")
        self.other_staff = self.create_user("other-staff")
        self.club = self.create_club("Create Club", slug="create-club")
        self.court = self.create_court(self.club, "Create Court")
        self.other_court = self.create_court(self.club, "Other Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.create_membership(
            self.other_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )

    def test_staff_creates_agreements_only_for_assigned_court(self):
        self.client.force_authenticate(user=self.staff)
        ok = self.post_agreement(self.club, self.court)
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)
        denied = self.post_agreement(self.club, self.other_court)
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_full_deposit_required_and_separate_from_booking_payments(self):
        self.client.force_authenticate(user=self.owner)
        response = self.post_agreement(self.club, self.court)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        agreement = RecurringAgreement.objects.get(pk=response.data["id"])
        self.assertEqual(agreement.deposit_amount, Decimal("300.00"))
        self.assertEqual(
            agreement.deposit_status, RecurringAgreement.DepositStatus.HELD
        )
        self.assertEqual(
            RecurringDepositTransaction.objects.filter(
                agreement=agreement,
                transaction_type=RecurringDepositTransaction.Type.COLLECTION,
            ).count(),
            1,
        )
        booking = agreement.bookings.first()
        self.assertEqual(booking.source, Booking.Source.RECURRING)
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.transactions.count(), 0)


class RecurringCancellationTests(RecurringAPITestCase):
    def setUp(self):
        self.owner = self.create_user("cancel-owner")
        self.club = self.create_club("Cancel Club", slug="cancel-club")
        self.court = self.create_court(
            self.club,
            "Cancel Court",
            recurring_deposit_refund_notice_days=1,
        )
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)
        create = self.post_agreement(self.club, self.court)
        self.agreement = RecurringAgreement.objects.get(pk=create.data["id"])

    def test_cancellation_preview_uses_previewed_at(self):
        response = self.client.post(
            self.agreement_preview_url(self.club, self.agreement),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("previewed_at", response.data)
        self.assertNotIn("cancellation_requested_at", response.data)

    def test_cancellation_before_deadline_gives_refund_due(self):
        effective = self.agreement.start_date + timedelta(days=14)
        response = self.client.post(
            self.agreement_cancel_url(self.club, self.agreement),
            {"effective_date": effective.isoformat(), "reason": "Moving"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agreement.refresh_from_db()
        self.assertEqual(self.agreement.status, RecurringAgreement.Status.CANCELLED)
        self.assertEqual(
            self.agreement.deposit_status,
            RecurringAgreement.DepositStatus.REFUND_DUE,
        )
        self.assertIsNotNone(self.agreement.cancellation_requested_at)

    def test_cancellation_exactly_at_deadline_gives_refund_due(self):
        # notice_days=1 → deadline is occurrence_start - 1 day.
        # Cancel at exactly that deadline via free-running clock is hard;
        # use notice_days=0 and cancel at start boundary through service path.
        self.agreement.refund_notice_days_snapshot = 0
        self.agreement.save(update_fields=["refund_notice_days_snapshot"])
        from apps.recurring.services import is_deposit_refundable

        occurrence_start = timezone.make_aware(
            timezone.datetime.combine(
                self.agreement.start_date, self.agreement.start_time
            ),
            timezone.get_current_timezone(),
        )
        self.assertTrue(
            is_deposit_refundable(
                agreement=self.agreement,
                requested_at=occurrence_start,
                effective_date=self.agreement.start_date,
            )
        )

    def test_cancellation_after_deadline_gives_forfeited(self):
        self.agreement.refund_notice_days_snapshot = 30
        self.agreement.save(update_fields=["refund_notice_days_snapshot"])
        response = self.client.post(
            self.agreement_cancel_url(self.club, self.agreement),
            {
                "effective_date": self.agreement.start_date.isoformat(),
                "reason": "Late",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agreement.refresh_from_db()
        self.assertEqual(
            self.agreement.deposit_status,
            RecurringAgreement.DepositStatus.FORFEITED,
        )
        self.assertEqual(
            RecurringDepositTransaction.objects.filter(
                agreement=self.agreement,
                transaction_type=RecurringDepositTransaction.Type.REFUND,
            ).count(),
            0,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.RECURRING_DEPOSIT_FORFEITED,
                entity_id=self.agreement.id,
            ).exists()
        )

    def test_paid_future_bookings_block_cancellation(self):
        booking = self.agreement.bookings.order_by("start_time").first()
        Transaction.objects.create(
            booking=booking,
            amount=Decimal("50.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.owner,
        )
        response = self.client.post(
            self.agreement_cancel_url(self.club, self.agreement),
            {"effective_date": booking.start_time.date().isoformat()},
            format="json",
        )
        self.assert_api_error(
            response, "RECURRING_CANCELLATION_HAS_PAID_FUTURE_BOOKINGS"
        )

    def test_unpaid_future_bookings_are_cancelled_and_released(self):
        booking_ids = list(
            self.agreement.bookings.order_by("start_time").values_list("id", flat=True)
        )
        response = self.client.post(
            self.agreement_cancel_url(self.club, self.agreement),
            {
                "effective_date": self.agreement.start_date.isoformat(),
                "reason": "Done",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for booking in Booking.objects.filter(id__in=booking_ids):
            self.assertEqual(booking.status, Booking.Status.CANCELLED)


class RecurringRefundSettlementTests(RecurringAPITestCase):
    def setUp(self):
        self.owner = self.create_user("refund-owner")
        self.staff = self.create_user("refund-staff")
        self.club = self.create_club("Refund Club", slug="refund-club")
        self.court = self.create_court(self.club, "Refund Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.client.force_authenticate(user=self.staff)
        create = self.post_agreement(self.club, self.court)
        self.agreement = RecurringAgreement.objects.get(pk=create.data["id"])
        self.collection = RecurringDepositTransaction.objects.get(
            agreement=self.agreement,
            transaction_type=RecurringDepositTransaction.Type.COLLECTION,
        )
        # Force refundable cancel.
        self.client.force_authenticate(user=self.owner)
        cancel = self.client.post(
            self.agreement_cancel_url(self.club, self.agreement),
            {
                "effective_date": (
                    self.agreement.start_date + timedelta(days=14)
                ).isoformat(),
                "reason": "Cancel",
            },
            format="json",
        )
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        self.agreement.refresh_from_db()

    def test_refund_creates_separate_immutable_refund_record(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            self.agreement_refund_url(self.club, self.agreement),
            {"payment_method": Transaction.PaymentMethod.CASH},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.agreement.refresh_from_db()
        self.assertEqual(
            self.agreement.deposit_status,
            RecurringAgreement.DepositStatus.REFUNDED,
        )
        self.collection.refresh_from_db()
        self.assertEqual(
            self.collection.transaction_type,
            RecurringDepositTransaction.Type.COLLECTION,
        )
        self.assertEqual(self.collection.amount, Decimal("300.00"))
        refund = RecurringDepositTransaction.objects.get(
            agreement=self.agreement,
            transaction_type=RecurringDepositTransaction.Type.REFUND,
        )
        self.assertEqual(refund.amount, Decimal("300.00"))

    def test_refund_is_included_as_outgoing_settlement_item(self):
        self.client.force_authenticate(user=self.staff)
        self.client.post(
            self.agreement_refund_url(self.club, self.agreement),
            {"payment_method": Transaction.PaymentMethod.CASH},
            format="json",
        )
        self.client.force_authenticate(user=self.owner)
        preview = self.client.get(
            self.settlement_preview_url(self.club),
            {"collected_by": self.staff.id},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Decimal(preview.data["deposit_collections"]), Decimal("300.00")
        )
        self.assertEqual(Decimal(preview.data["deposit_refunds"]), Decimal("300.00"))
        self.assertEqual(Decimal(preview.data["net_amount"]), Decimal("0.00"))

        create = self.client.post(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff.id},
            format="json",
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            SettlementRecurringDepositTransaction.objects.filter(
                settlement_id=create.data["id"]
            ).count(),
            2,
        )


class RecurringBookingPaymentCancelTests(RecurringAPITestCase):
    def setUp(self):
        self.owner = self.create_user("hold-owner")
        self.club = self.create_club("Hold Club", slug="hold-club")
        self.court = self.create_court(self.club, "Hold Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)
        create = self.post_agreement(self.club, self.court)
        self.agreement = RecurringAgreement.objects.get(pk=create.data["id"])
        self.booking = self.agreement.bookings.order_by("start_time").first()

    def test_recurring_booking_payment_cancel_does_not_demote_to_hold(self):
        access = self.make_access(self.owner, self.club)
        payment = create_booking_transaction(
            access=access,
            booking=self.booking,
            amount=Decimal("50.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.owner,
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        cancel_transaction(
            access=access,
            transaction_obj=payment,
            reason="correction",
            actor=self.owner,
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)


class RecurringEndpointScopeTests(RecurringAPITestCase):
    def test_no_replace_skip_pause_or_resume_endpoints(self):
        urls_path = Path("apps/recurring/urls.py").read_text(encoding="utf-8")
        self.assertNotIn("replace", urls_path)
        self.assertNotIn("pause", urls_path)
        self.assertNotIn("resume", urls_path)
        self.assertNotIn("skip", urls_path)
        self.assertFalse(hasattr(RecurringAgreement, "replaced_by"))
