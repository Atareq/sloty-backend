import logging
import os
import re
from datetime import time
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.bookings.models import Booking
from apps.bookings.services import due_hold_booking_candidate_ids
from apps.settlements.models import Settlement, SettlementTransaction
from tests.bookings.test_booking_api import BookingAPITestCase

PERF_RE = re.compile(
    r"\[PERF\] (?P<method>\S+) (?P<path>\S+) status=(?P<status>\d+) "
    r"queries=(?P<queries>\d+) db=(?P<db>[\d.]+)ms total=(?P<total>[\d.]+)ms "
    r"duplicates=(?P<duplicates>\d+) repeated_shapes=(?P<shapes>\d+) "
    r"potential_n_plus_one=(?P<n1>\d+)"
)


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


@override_settings(
    SQL_QUERY_STATS_ENABLED=True,
    SQL_QUERY_STATS_VERBOSE=False,
    SQL_QUERY_STATS_WARN_QUERY_COUNT=1,
    SQL_QUERY_STATS_SLOW_QUERY_MS=100,
    SQL_QUERY_STATS_SLOW_REQUEST_MS=5000,
)
class EndpointPerfMeasurementTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("perf-admin")
        self.club = self.create_club("Perf Club", slug="perf-club")
        self.court = self.create_court(self.club, "Perf Court")
        self.create_working_hours(
            self.court,
            weekday=2,
            opens_at=time(9, 0),
            closes_at=time(23, 0),
        )
        self.booking = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CONFIRMED,
        )
        self.create_transaction(self.booking, amount=Decimal("50.00"))
        self.anchor = self.create_booking(
            self.court,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            source=Booking.Source.RECURRING,
            recurrence_status=Booking.RecurrenceStatus.ACTIVE,
            status=Booking.Status.CONFIRMED,
            customer_phone="+201000009500",
        )
        self.client.force_authenticate(user=self.platform_admin)

    def _measure(self, label, request_callable):
        handler = _ListHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("sloty.sql")
        logger.addHandler(handler)
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            with CaptureQueriesContext(connection) as captured:
                response = request_callable()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        summaries = [line for line in handler.messages if line.startswith("[PERF] ")]
        self.assertTrue(summaries, f"Missing [PERF] summary for {label}")
        match = PERF_RE.search(summaries[0])
        self.assertIsNotNone(match, summaries[0])
        row = {
            "label": label,
            "status": response.status_code,
            "queries": int(match.group("queries")),
            "db_ms": float(match.group("db")),
            "total_ms": float(match.group("total")),
            "duplicates": int(match.group("duplicates")),
            "repeated_shapes": int(match.group("shapes")),
            "potential_n_plus_one": int(match.group("n1")),
            "django_queries": len(captured),
            "log": summaries[0],
        }
        self.assertIn("db=", summaries[0])
        self.assertIn("total=", summaries[0])
        if row["queries"] > 1:
            self.assertTrue(
                any(line.startswith("[PERF:WARN]") for line in handler.messages)
            )
        else:
            self.assertFalse(
                any(line.startswith("[PERF:WARN]") for line in handler.messages)
            )
        return response, row

    def test_representative_endpoints_emit_bounded_perf_summaries(self):
        rows = []

        response, row = self._measure(
            "GET /me/",
            lambda: self.client.get(reverse("me")),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET clubs",
            lambda: self.client.get(reverse("club-list")),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET courts",
            lambda: self.client.get(
                reverse("club-court-list", kwargs={"club_slug": self.club.slug})
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET schedule empty-ish day",
            lambda: self.client.get(
                self.booking_slots_url(self.club),
                {"court": self.court.id, "date": "2026-05-20"},
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET schedule virtual recurring range",
            lambda: self.client.get(
                self.booking_slots_url(self.club),
                {
                    "court": self.court.id,
                    "date_from": "2026-05-20",
                    "date_to": "2026-06-10",
                },
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reserved = [
            slot
            for slot in response.data["slots"]
            if slot["slot_status"] == "RECURRING_RESERVED"
        ]
        self.assertGreaterEqual(len(reserved), 2)
        rows.append(row)

        response, row = self._measure(
            "GET booking list",
            lambda: self.client.get(self.booking_list_url(self.club)),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET booking detail",
            lambda: self.client.get(self.booking_detail_url(self.club, self.booking)),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "POST recurring booking",
            lambda: self.post_booking(
                self.club,
                self.court,
                start_time=self.time_at(13).isoformat(),
                end_time=self.time_at(14).isoformat(),
                is_recurring=True,
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], Booking.Source.RECURRING)
        rows.append(row)

        response, row = self._measure(
            "GET booking search",
            lambda: self.client.get(
                self.booking_list_url(self.club),
                {"search": "Existing"},
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET transactions",
            lambda: self.client.get(
                reverse(
                    "club-transaction-list",
                    kwargs={"club_slug": self.club.slug},
                )
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET transaction search",
            lambda: self.client.get(
                reverse(
                    "club-transaction-list",
                    kwargs={"club_slug": self.club.slug},
                ),
                {"search": "Existing"},
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET settlements",
            lambda: self.client.get(
                reverse(
                    "club-settlement-list",
                    kwargs={"club_slug": self.club.slug},
                )
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET unsettled-summary",
            lambda: self.client.get(
                reverse(
                    "club-settlement-unsettled-summary",
                    kwargs={"club_slug": self.club.slug},
                )
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        settlement = Settlement.objects.create(
            club=self.club,
            court=self.court,
            period_start=self.time_at(8),
            period_end=self.time_at(14),
            status=Settlement.Status.SETTLED,
            total_amount=Decimal("50.00"),
            transaction_count=1,
            collected_by=self.platform_admin,
            created_by=self.platform_admin,
            settled_by=self.platform_admin,
        )
        paid = self.booking.transactions.get()
        SettlementTransaction.objects.create(
            settlement=settlement,
            transaction=paid,
            amount=paid.amount,
        )
        response, row = self._measure(
            "GET settlement detail",
            lambda: self.client.get(
                reverse(
                    "club-settlement-detail",
                    kwargs={"club_slug": self.club.slug, "pk": settlement.pk},
                )
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET dashboard summary",
            lambda: self.client.get(
                reverse(
                    "club-dashboard-summary",
                    kwargs={"club_slug": self.club.slug},
                ),
                {
                    "date_from": "2026-05-20T00:00:00",
                    "date_to": "2026-05-20T23:59:59",
                },
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        response, row = self._measure(
            "GET court-usage report",
            lambda: self.client.get(
                reverse(
                    "club-report-court-usage",
                    kwargs={"club_slug": self.club.slug},
                ),
                {"date_from": "2026-05-20", "date_to": "2026-05-20"},
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows.append(row)

        with CaptureQueriesContext(connection) as hold_expiry_queries:
            due_hold_booking_candidate_ids(now=timezone.now())
        rows.append(
            {
                "label": "HOLD expiry candidate query",
                "status": "-",
                "queries": len(hold_expiry_queries),
                "db_ms": 0.0,
                "total_ms": 0.0,
                "duplicates": 0,
                "repeated_shapes": 0,
                "potential_n_plus_one": 0,
                "log": f"django_queries={len(hold_expiry_queries)}",
            }
        )

        if os.environ.get("SLOTY_PRINT_PERF") == "1":
            print(
                "\nEndpoint | Status | Queries | DB ms | Total ms | Dup | Shapes | N+1?"
            )
            for item in rows:
                print(
                    f"{item['label']} | {item['status']} | {item['queries']} | "
                    f"{item['db_ms']:.2f} | {item['total_ms']:.2f} | "
                    f"{item['duplicates']} | {item['repeated_shapes']} | "
                    f"{item['potential_n_plus_one']}"
                )
                print(f"  {item['log']}")
