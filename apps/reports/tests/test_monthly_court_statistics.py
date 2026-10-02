from datetime import timedelta
from decimal import Decimal

import yaml
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.bookings.models import Booking
from apps.reports.tests.test_court_usage_report import CourtUsageReportAPITestCase
from apps.settlements.models import Settlement, SettlementTransaction
from apps.settlements.services import create as create_settlement
from apps.transactions.models import Transaction


class MonthlyCourtStatisticsTests(CourtUsageReportAPITestCase):
    def url(self, *, court=None, club=None):
        return reverse(
            "club-court-monthly-statistics",
            kwargs={
                "club_slug": (club or self.club).slug,
                "court_id": (court or self.court).id,
            },
        )

    def test_returns_exact_backend_authoritative_monthly_contract(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url(), {"month": "2026-07"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "month": "2026-07",
                "total_income": "550.00",
                "total_bookings": 2,
            },
        )

    def test_court_path_selects_exact_court_and_excludes_other_courts(self):
        self.client.force_authenticate(user=self.owner)

        first_court = self.client.get(self.url(), {"month": "2026-07"})
        second_court = self.client.get(
            self.url(court=self.other_court),
            {"month": "2026-07"},
        )

        self.assertEqual(first_court.status_code, status.HTTP_200_OK)
        self.assertEqual(second_court.status_code, status.HTTP_200_OK)
        self.assertEqual(first_court.data["total_income"], "550.00")
        self.assertEqual(first_court.data["total_bookings"], 2)
        self.assertEqual(second_court.data["total_income"], "50.00")
        self.assertEqual(second_court.data["total_bookings"], 1)

    def settle(self, transaction_obj):
        settlement = Settlement.objects.create(
            club=self.club,
            total_amount=transaction_obj.amount,
            settled_by=self.owner.profile,
        )
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=transaction_obj,
            amount=transaction_obj.amount,
        )

    def test_income_includes_settled_rows_and_signed_refunds_by_created_date(self):
        refund = self.create_transaction(
            self.confirmed,
            amount=Decimal("-25.00"),
            transaction_type=Transaction.Type.REFUND,
            created=self.time_at(day=31),
        )
        self.settle(
            Transaction.objects.get(booking=self.completed, amount=Decimal("450.00"))
        )
        self.assertEqual(
            timezone.localtime(refund.occurred_at).date(), timezone.localdate()
        )

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), {"month": "2026-07"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_income"], "525.00")

    def test_income_counts_all_valid_transactions_for_court_and_month(self):
        # Fixture on this Court in July: +100.00 and +450.00 unsettled,
        # +60.00 cancelled; +50.00 on the other Court.
        july_start = self.time_at(day=1)
        august_start = july_start.replace(month=8)
        for amount, created in (
            ("10.00", july_start),
            ("5.00", august_start - timedelta(seconds=1)),
            ("70.00", august_start),
            ("33.00", july_start - timedelta(seconds=1)),
        ):
            self.create_transaction(
                self.confirmed, amount=Decimal(amount), created=created
            )
        self.create_transaction(
            self.confirmed,
            amount=Decimal("40.00"),
            is_cancelled=True,
            created=self.time_at(day=12),
        )
        self.create_transaction(
            self.confirmed,
            amount=Decimal("-25.00"),
            transaction_type=Transaction.Type.REFUND,
            created=self.time_at(day=20),
        )
        self.settle(
            self.create_transaction(
                self.completed, amount=Decimal("80.00"), created=self.time_at(day=15)
            )
        )
        self.create_transaction(
            self.cross_booking, amount=Decimal("999.00"), created=self.time_at(day=10)
        )

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url(), {"month": "2026-07"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 100 + 450 unsettled + 80 settled + 10 + 5 month edges - 25 refund.
        self.assertEqual(response.data["total_income"], "620.00")

    def test_settling_transactions_does_not_reduce_monthly_income(self):
        self.client.force_authenticate(user=self.owner)
        before = self.client.get(self.url(), {"month": "2026-07"})

        settlement = create_settlement(self.club, settled_by=self.owner.profile)
        after = self.client.get(self.url(), {"month": "2026-07"})

        self.assertEqual(settlement.lines.count(), 3)
        self.assertEqual(before.data["total_income"], "550.00")
        self.assertEqual(after.status_code, status.HTTP_200_OK)
        self.assertEqual(after.data["total_income"], "550.00")

    def test_booking_count_uses_visible_timeline_lifecycle(self):
        Booking.objects.filter(pk=self.hold.pk).update(
            hold_expires_at=timezone.now() + timedelta(days=1)
        )
        self.create_booking(
            self.court,
            status=Booking.Status.NO_SHOW,
            start_time=self.time_at(7),
            end_time=self.time_at(8),
        )
        self.create_booking(
            self.court,
            status=Booking.Status.EXPIRED,
            start_time=self.time_at(12),
            end_time=self.time_at(13),
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.url(), {"month": "2026-07"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_bookings"], 4)

    def test_zero_activity_and_strict_month_validation(self):
        self.client.force_authenticate(user=self.owner)

        zero_response = self.client.get(self.url(), {"month": "2026-08"})
        self.assertEqual(zero_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            zero_response.data,
            {
                "month": "2026-08",
                "total_income": "0.00",
                "total_bookings": 0,
            },
        )

        for params in (
            {},
            {"month": "2026-7"},
            {"month": "2026-13"},
            {"month": "0000-01"},
            {"month": " 2026-07 "},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url(), params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["code"], "VALIDATION_ERROR")
                self.assertFalse(response.data["success"])
                self.assertIn("message", response.data)
                self.assertIn("month", response.data["field_errors"])

    def test_reuses_report_role_and_court_authorization(self):
        unauthenticated = self.client.get(self.url(), {"month": "2026-07"})
        self.assertEqual(set(unauthenticated.data), {"success", "code", "message"})
        self.assertEqual(unauthenticated.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.staff)
        staff_response = self.client.get(self.url(), {"month": "2026-07"})
        self.assertEqual(staff_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(set(staff_response.data), {"success", "code", "message"})

        self.client.force_authenticate(user=self.owner)
        cross_club_court = self.client.get(
            self.url(court=self.cross_court),
            {"month": "2026-07"},
        )
        self.assertEqual(cross_club_court.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(set(cross_club_court.data), {"success", "code", "message"})

    def test_openapi_exposes_exact_monthly_contract_and_errors(self):
        schema_response = self.client.get(reverse("schema"))
        self.assertEqual(schema_response.status_code, status.HTTP_200_OK)
        schema = yaml.safe_load(schema_response.content.decode())
        operation = schema["paths"][
            "/api/v1/clubs/{club_slug}/courts/{court_id}/monthly-statistics/"
        ]["get"]

        parameters = {
            (parameter["name"], parameter["in"]): parameter
            for parameter in operation["parameters"]
        }
        self.assertEqual(
            set(parameters),
            {
                ("club_slug", "path"),
                ("court_id", "path"),
                ("month", "query"),
            },
        )
        self.assertTrue(parameters[("club_slug", "path")]["required"])
        self.assertTrue(parameters[("court_id", "path")]["required"])
        self.assertTrue(parameters[("month", "query")]["required"])
        self.assertIn("YYYY-MM", parameters[("month", "query")]["description"])

        responses = operation["responses"]
        self.assertEqual(set(responses), {"200", "400", "401", "403", "404"})
        self.assertEqual(
            responses["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/MonthlyCourtStatisticsResponse",
        )
        self.assertEqual(
            responses["400"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/SlotyValidationErrorResponse",
        )
        for status_code in ("401", "403", "404"):
            self.assertEqual(
                responses[status_code]["content"]["application/json"]["schema"]["$ref"],
                "#/components/schemas/SlotyErrorResponse",
            )

        response_schema = schema["components"]["schemas"][
            "MonthlyCourtStatisticsResponse"
        ]
        self.assertEqual(
            set(response_schema["properties"]),
            {"month", "total_income", "total_bookings"},
        )
        self.assertEqual(
            set(response_schema["required"]),
            {"month", "total_income", "total_bookings"},
        )
        self.assertEqual(operation["security"], [{"jwtAuth": []}])
        self.assertIn("Platform Admin or Club Owner", operation["description"])
        self.assertIn("Staff receive 403", operation["description"])
