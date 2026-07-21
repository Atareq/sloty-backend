from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.urls import resolve, reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.filters import AuditLogFilter
from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.audit.views import AuditLogViewSet
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction


class AuditAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="audit-admin") -> User:
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
            3,
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
            "customer_name": "Audit Customer",
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
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        return Transaction.objects.create(**data)

    def create_audit_log(self, club: Club, **extra_fields) -> AuditLog:
        data = {
            "club": club,
            "action": AuditLog.Action.BOOKING_CREATED,
            "entity_type": "Booking",
            "entity_id": 1,
        }
        data.update(extra_fields)
        return AuditLog.objects.create(**data)

    def audit_list_url(self, club):
        return reverse("club-audit-log-list", kwargs={"club_slug": club.slug})

    def audit_detail_url(self, club, audit_log):
        return reverse(
            "club-audit-log-detail",
            kwargs={"club_slug": club.slug, "pk": audit_log.pk},
        )

    def booking_list_url(self, club):
        return reverse("club-booking-list", kwargs={"club_slug": club.slug})

    def booking_detail_url(self, club, booking):
        return reverse(
            "club-booking-detail",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def booking_action_url(self, club, booking, action):
        return reverse(
            f"club-booking-{action}",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def transaction_list_url(self, club):
        return reverse("club-transaction-list", kwargs={"club_slug": club.slug})

    def transaction_cancel_url(self, club, transaction_obj):
        return reverse(
            "club-transaction-cancel",
            kwargs={"club_slug": club.slug, "pk": transaction_obj.pk},
        )

    def settlement_list_url(self, club):
        return reverse("club-settlement-list", kwargs={"club_slug": club.slug})

    def settlement_mark_settled_url(self, club, settlement):
        return reverse(
            "club-settlement-mark-settled",
            kwargs={"club_slug": club.slug, "pk": settlement.pk},
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


class AuditModelServiceTests(AuditAPITestCase):
    def setUp(self):
        self.actor = self.create_user("audit-model-actor")
        self.club = self.create_club("Audit Model Club", slug="audit-model")
        self.court = self.create_court(self.club, "Audit Model Court")

    def test_audit_log_can_be_created_with_default_json_data(self):
        audit_log = self.create_audit_log(self.club, actor=self.actor, court=self.court)

        self.assertEqual(audit_log.before_data, {})
        self.assertEqual(audit_log.after_data, {})
        self.assertEqual(audit_log.metadata, {})

    def test_audit_logs_order_newest_first_by_default(self):
        older = self.create_audit_log(self.club, entity_id=1)
        newer = self.create_audit_log(self.club, entity_id=2)
        AuditLog.objects.filter(pk=older.pk).update(created=self.time_at(10))
        AuditLog.objects.filter(pk=newer.pk).update(created=self.time_at(11))

        self.assertEqual(
            list(AuditLog.objects.values_list("id", flat=True)), [newer.id, older.id]
        )

    def test_record_audit_log_creates_log_with_expected_data(self):
        audit_log = record_audit_log(
            club=self.club,
            court=self.court,
            actor=self.actor,
            action=AuditLog.Action.TRANSACTION_CREATED,
            entity_type="Transaction",
            entity_id=15,
            before_data={"old": "value"},
            after_data={"new": "value"},
            metadata={"source": "test"},
        )

        self.assertEqual(audit_log.club, self.club)
        self.assertEqual(audit_log.court, self.court)
        self.assertEqual(audit_log.actor, self.actor)
        self.assertEqual(audit_log.action, AuditLog.Action.TRANSACTION_CREATED)
        self.assertEqual(audit_log.entity_type, "Transaction")
        self.assertEqual(audit_log.entity_id, 15)
        self.assertEqual(audit_log.before_data, {"old": "value"})
        self.assertEqual(audit_log.after_data, {"new": "value"})
        self.assertEqual(audit_log.metadata, {"source": "test"})

    def test_record_audit_log_accepts_actor_none(self):
        audit_log = record_audit_log(
            club=self.club,
            actor=None,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=1,
        )

        self.assertIsNone(audit_log.actor)


class AuditAccessAPITests(AuditAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("audit-access-admin")
        self.owner = self.create_user("audit-access-owner")
        self.manager = self.create_user("audit-access-manager")
        self.staff = self.create_user("audit-access-staff")
        self.other_user = self.create_user("audit-access-other")
        self.club = self.create_club("Audit Access Club", slug="audit-access")
        self.other_club = self.create_club("Other Audit Club", slug="other-audit")
        self.court = self.create_court(self.club, "Audit Court")
        self.other_court = self.create_court(self.other_club, "Other Audit Court")
        self.audit_log = self.create_audit_log(
            self.club,
            court=self.court,
            actor=self.owner,
            entity_id=10,
        )
        self.other_log = self.create_audit_log(
            self.other_club,
            court=self.other_court,
            entity_id=20,
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

    def test_anonymous_rejected(self):
        response = self.client.get(self.audit_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_owner_and_manager_can_list_and_retrieve(self):
        for user in (self.platform_admin, self.owner, self.manager):
            self.client.force_authenticate(user=user)

            list_response = self.client.get(self.audit_list_url(self.club))
            detail_response = self.client.get(
                self.audit_detail_url(self.club, self.audit_log)
            )

            self.assertEqual(list_response.status_code, status.HTTP_200_OK)
            self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

    def test_staff_rejected(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.audit_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unrelated_club_member_rejected(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get(self.audit_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_wrong_club_logs_not_visible(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.audit_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.audit_log.id, self.list_ids(response))
        self.assertNotIn(self.other_log.id, self.list_ids(response))

    def test_audit_responses_include_localized_action_label(self):
        transaction_log = self.create_audit_log(
            self.club,
            court=self.court,
            actor=self.owner,
            action=AuditLog.Action.TRANSACTION_CANCELLED,
            entity_type="Transaction",
            entity_id=101,
        )
        self.client.force_authenticate(user=self.platform_admin)

        list_response = self.client.get(
            self.audit_list_url(self.club),
            HTTP_ACCEPT_LANGUAGE="en",
        )
        detail_en_response = self.client.get(
            self.audit_detail_url(self.club, transaction_log),
            HTTP_ACCEPT_LANGUAGE="en",
        )
        detail_ar_response = self.client.get(
            self.audit_detail_url(self.club, transaction_log),
            HTTP_ACCEPT_LANGUAGE="ar",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn("action_label", list_response.data["results"][0])
        self.assertEqual(detail_en_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_ar_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail_en_response.data["action"],
            AuditLog.Action.TRANSACTION_CANCELLED,
        )
        self.assertEqual(
            detail_ar_response.data["action"],
            AuditLog.Action.TRANSACTION_CANCELLED,
        )
        self.assertEqual(
            detail_en_response.data["action_label"],
            "Transaction cancelled",
        )
        self.assertEqual(
            detail_ar_response.data["action_label"],
            "تم إلغاء عملية الدفع",
        )

    def test_read_only_methods(self):
        self.client.force_authenticate(user=self.platform_admin)
        list_url = self.audit_list_url(self.club)
        detail_url = self.audit_detail_url(self.club, self.audit_log)

        self.assertEqual(
            self.client.post(list_url, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(detail_url, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(detail_url, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class AuditFilterRegressionTests(AuditAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("audit-filter-admin")
        self.actor = self.create_user("audit-filter-actor")
        self.other_actor = self.create_user("audit-filter-other-actor")
        self.club = self.create_club("Audit Filter Club", slug="audit-filter")
        self.other_club = self.create_club("Other Filter Club", slug="other-filter")
        self.court = self.create_court(self.club, "Audit Filter Court")
        self.other_court = self.create_court(self.club, "Audit Filter Other Court")
        self.external_court = self.create_court(self.other_club, "External Court")
        self.booking_log = self.create_audit_log(
            self.club,
            court=self.court,
            actor=self.actor,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=101,
        )
        self.transaction_log = self.create_audit_log(
            self.club,
            court=self.other_court,
            actor=self.other_actor,
            action=AuditLog.Action.TRANSACTION_CREATED,
            entity_type="Transaction",
            entity_id=202,
        )
        self.other_log = self.create_audit_log(
            self.other_club,
            court=self.external_court,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=303,
        )
        AuditLog.objects.filter(pk=self.booking_log.pk).update(created=self.time_at(10))
        AuditLog.objects.filter(pk=self.transaction_log.pk).update(
            created=self.time_at(12)
        )
        self.booking_log.refresh_from_db()
        self.transaction_log.refresh_from_db()
        self.client.force_authenticate(user=self.platform_admin)

    def test_filter_by_action_entity_type_entity_id_actor_and_court(self):
        self.assertEqual(
            self.list_ids(
                self.client.get(
                    self.audit_list_url(self.club),
                    {"action": AuditLog.Action.BOOKING_CREATED},
                )
            ),
            {self.booking_log.id},
        )
        self.assertEqual(
            self.list_ids(
                self.client.get(
                    self.audit_list_url(self.club),
                    {"entity_type": "Transaction"},
                )
            ),
            {self.transaction_log.id},
        )
        self.assertEqual(
            self.list_ids(
                self.client.get(
                    self.audit_list_url(self.club),
                    {"entity_id": self.booking_log.entity_id},
                )
            ),
            {self.booking_log.id},
        )
        self.assertEqual(
            self.list_ids(
                self.client.get(
                    self.audit_list_url(self.club),
                    {"actor": self.other_actor.id},
                )
            ),
            {self.transaction_log.id},
        )
        self.assertEqual(
            self.list_ids(
                self.client.get(
                    self.audit_list_url(self.club),
                    {"court": self.court.id},
                )
            ),
            {self.booking_log.id},
        )

    def test_filter_by_date_date_from_and_date_to(self):
        date_response = self.client.get(
            self.audit_list_url(self.club),
            {"date": self.time_at(10).date().isoformat()},
        )
        date_from_response = self.client.get(
            self.audit_list_url(self.club),
            {"date_from": self.time_at(11).isoformat()},
        )
        date_to_response = self.client.get(
            self.audit_list_url(self.club),
            {"date_to": self.time_at(11).isoformat()},
        )

        self.assertEqual(
            self.list_ids(date_response), {self.booking_log.id, self.transaction_log.id}
        )
        self.assertEqual(self.list_ids(date_from_response), {self.transaction_log.id})
        self.assertEqual(self.list_ids(date_to_response), {self.booking_log.id})

    def test_filters_respect_club_scope_and_ignore_club_query_param(self):
        response = self.client.get(
            self.audit_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(
            self.list_ids(response), {self.booking_log.id, self.transaction_log.id}
        )
        self.assertNotIn(self.other_log.id, self.list_ids(response))

    def test_audit_route_resolves_to_viewset(self):
        match = resolve("/api/v1/clubs/example-club/audit-logs/")

        self.assertIs(match.func.cls, AuditLogViewSet)

    def test_audit_viewset_uses_filterset_and_does_not_parse_query_params(self):
        self.assertEqual(AuditLogViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(AuditLogViewSet.filterset_class, AuditLogFilter)
        self.assertEqual(AuditLogViewSet.http_method_names, ("get", "head", "options"))

        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "audit" / "views.py").read_text()

        self.assertNotIn("request.query_params.get", view_source)
        self.assertNotIn("parse_date", view_source)
        self.assertNotIn("parse_datetime", view_source)

    def test_audit_filter_does_not_contain_access_logic(self):
        repo_root = Path(__file__).resolve().parents[2]
        filter_source = (repo_root / "apps" / "audit" / "filters.py").read_text()

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
        self.assertFalse((repo_root / "apps" / "audit" / "permissions.py").exists())
        self.assertFalse(
            (repo_root / "apps" / "transactions" / "permissions.py").exists()
        )


class AuditBusinessLoggingTests(AuditAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("audit-business-admin")
        self.club = self.create_club(
            "Audit Business Club",
            slug="audit-business",
            manager_can_settle_transactions=True,
        )
        self.court = self.create_court(self.club, "Audit Business Court")
        self.staff = self.create_user("audit-business-staff")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def booking_payload(self, hour=10, **extra_fields):
        data = {
            "court": self.court.id,
            "customer_name": "Business Customer",
            "customer_phone": "+201000001000",
            "start_time": self.time_at(hour).isoformat(),
            "end_time": self.time_at(hour + 1).isoformat(),
            "source": Booking.Source.MANUAL,
        }
        data.update(extra_fields)
        return data

    def test_creating_booking_creates_audit_log(self):
        response = self.client.post(
            self.booking_list_url(self.club),
            self.booking_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_CREATED,
                entity_type="Booking",
                entity_id=response.data["id"],
            ).exists()
        )

    def test_updating_booking_creates_audit_log(self):
        booking = self.create_booking(
            self.court, start_time=self.time_at(12), end_time=self.time_at(13)
        )

        response = self.client.patch(
            self.booking_detail_url(self.club, booking),
            {"customer_name": "Updated Customer"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        audit_log = AuditLog.objects.get(
            action=AuditLog.Action.BOOKING_UPDATED,
            entity_type="Booking",
            entity_id=booking.id,
        )
        self.assertEqual(audit_log.before_data["customer_name"], "Audit Customer")
        self.assertEqual(audit_log.after_data["customer_name"], "Updated Customer")

    def test_booking_lifecycle_actions_create_audit_logs(self):
        cases = (
            (
                Booking.Status.CONFIRMED,
                "complete",
                AuditLog.Action.BOOKING_COMPLETED,
                {"confirm_collect_remaining_cash": True},
            ),
            (
                Booking.Status.CONFIRMED,
                "no-show",
                AuditLog.Action.BOOKING_NO_SHOW,
                {"reason": "Customer did not arrive"},
            ),
            (
                Booking.Status.HOLD,
                "cancel",
                AuditLog.Action.BOOKING_CANCELLED,
                {"reason": "Customer cancelled"},
            ),
            (Booking.Status.HOLD, "expire", AuditLog.Action.BOOKING_EXPIRED, {}),
        )
        for index, (initial_status, action_name, audit_action, payload) in enumerate(
            cases
        ):
            booking = self.create_booking(
                self.court,
                customer_phone=f"+20100000110{index}",
                start_time=self.time_at(14 + index),
                end_time=self.time_at(15 + index),
                status=initial_status,
            )
            if action_name == "complete":
                self.create_transaction(booking, amount=booking.total_price)

            response = self.client.post(
                self.booking_action_url(self.club, booking, action_name),
                payload,
                format="json",
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(
                AuditLog.objects.filter(
                    action=audit_action,
                    entity_type="Booking",
                    entity_id=booking.id,
                ).exists()
            )

    def test_rescheduling_booking_creates_audit_log(self):
        booking = self.create_booking(
            self.court,
            customer_phone="+201000001109",
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.CONFIRMED,
        )

        response = self.client.post(
            self.booking_action_url(self.club, booking, "reschedule"),
            {
                "court": self.court.id,
                "start_time": self.time_at(12).isoformat(),
                "end_time": self.time_at(13).isoformat(),
                "reason": "Audit reschedule",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        audit_log = AuditLog.objects.get(
            action=AuditLog.Action.BOOKING_RESCHEDULED,
            entity_type="Booking",
            entity_id=booking.id,
        )
        self.assertEqual(audit_log.metadata["reason"], "Audit reschedule")

    def test_creating_transaction_creates_audit_log(self):
        booking = self.create_booking(
            self.court,
            customer_phone="+201000001200",
            start_time=self.time_at(19),
            end_time=self.time_at(20),
            status=Booking.Status.HOLD,
        )

        response = self.client.post(
            self.transaction_list_url(self.club),
            {
                "booking": booking.id,
                "amount": "50.00",
                "payment_method": Transaction.PaymentMethod.CASH,
                "payment_reference": "AUDIT-TXN-1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.TRANSACTION_CREATED,
                entity_type="Transaction",
                entity_id=response.data["id"],
            ).exists()
        )

    def test_cancelling_transaction_creates_transaction_and_booking_audit_logs(self):
        booking = self.create_booking(
            self.court,
            customer_phone="+201000001201",
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=Booking.Status.CONFIRMED,
        )
        transaction_obj = self.create_transaction(
            booking,
            created_by=self.platform_admin,
        )

        response = self.client.post(
            self.transaction_cancel_url(self.club, transaction_obj),
            {"reason": "Audit correction"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cancel_log = AuditLog.objects.get(
            action=AuditLog.Action.TRANSACTION_CANCELLED,
            entity_type="Transaction",
            entity_id=transaction_obj.id,
        )
        self.assertEqual(cancel_log.metadata["reason"], "Audit correction")
        self.assertEqual(cancel_log.before_data["booking_status"], "CONFIRMED")
        self.assertEqual(cancel_log.after_data["booking_status"], "HOLD")
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_UPDATED,
                entity_type="Booking",
                entity_id=booking.id,
                metadata__source="transaction_cancel",
            ).exists()
        )

    def test_creating_and_marking_settlement_creates_audit_logs(self):
        booking = self.create_booking(
            self.court,
            customer_phone="+201000001300",
            start_time=self.time_at(21),
            end_time=self.time_at(22),
            status=Booking.Status.CONFIRMED,
        )
        transaction_obj = self.create_transaction(
            booking,
            amount=Decimal("80.00"),
            payment_reference="AUDIT-SETTLEMENT-1",
            created_by=self.staff,
        )
        Transaction.objects.filter(pk=transaction_obj.pk).update(
            created=self.time_at(22)
        )

        create_response = self.client.post(
            self.settlement_list_url(self.club),
            {"collected_by": self.staff.id},
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        settlement = Settlement.objects.get(pk=create_response.data["id"])
        create_log = AuditLog.objects.get(
            action=AuditLog.Action.SETTLEMENT_CREATED,
            entity_type="Settlement",
            entity_id=settlement.id,
        )
        self.assertEqual(create_log.after_data["collected_by_id"], self.staff.id)
        self.assertEqual(create_log.after_data["transaction_ids"], [transaction_obj.id])

        pending_settlement = Settlement.objects.create(
            club=self.club,
            court=self.court,
            collected_by=self.staff,
            period_start=self.time_at(22),
            period_end=self.time_at(23),
            status=Settlement.Status.PENDING,
            total_amount=Decimal("80.00"),
            transaction_count=1,
            created_by=self.platform_admin,
        )
        settle_response = self.client.post(
            self.settlement_mark_settled_url(self.club, pending_settlement),
            {},
            format="json",
        )

        self.assertEqual(settle_response.status_code, status.HTTP_200_OK)
        settle_log = AuditLog.objects.get(
            action=AuditLog.Action.SETTLEMENT_MARKED_SETTLED,
            entity_type="Settlement",
            entity_id=pending_settlement.id,
        )
        self.assertEqual(settle_log.after_data["collected_by_id"], self.staff.id)


class AuditSeedSchemaTests(AuditAPITestCase):
    def test_seed_demo_data_creates_audit_examples_idempotently(self):
        call_command("seed_demo_data", verbosity=0)
        count = AuditLog.objects.count()

        call_command("seed_demo_data", verbosity=0)

        self.assertEqual(AuditLog.objects.count(), count)
        self.assertTrue(
            AuditLog.objects.filter(
                club__slug="demo-football-club",
                action=AuditLog.Action.BOOKING_CREATED,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                club__slug="demo-football-club",
                action=AuditLog.Action.SETTLEMENT_MARKED_SETTLED,
            ).exists()
        )

    def test_schema_and_docs_return_200_and_include_audit_endpoints(self):
        schema_response = self.client.get(reverse("schema"))
        docs_response = self.client.get(reverse("swagger-ui"))

        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        self.assertEqual(docs_response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "/api/v1/clubs/{club_slug}/audit-logs/",
            schema_response.content.decode(),
        )
