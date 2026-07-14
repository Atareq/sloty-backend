from django.core.management import call_command
from django.db.models import Count
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.common.egypt_locations import is_valid_city_for_governorate
from apps.courts.models import Court
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

DEMO_CLUB_SLUGS = (
    "demo-football-club",
    "demo-restricted-club",
    "demo-other-club",
)


class SeedDemoDataCommandTests(TestCase):
    def run_seed_command(self):
        call_command("seed_demo_data", verbosity=0)

    def test_seed_demo_data_creates_predictable_demo_records(self):
        self.run_seed_command()

        platform_admin = User.objects.get(username="platform_admin")
        self.assertTrue(platform_admin.is_platform_admin)
        self.assertTrue(platform_admin.is_staff)
        self.assertTrue(platform_admin.is_superuser)

        self.assertEqual(Club.objects.filter(slug__in=DEMO_CLUB_SLUGS).count(), 3)
        self.assertEqual(
            Court.objects.filter(club__slug__in=DEMO_CLUB_SLUGS).count(),
            6,
        )
        for slug in DEMO_CLUB_SLUGS:
            self.assertEqual(Court.objects.filter(club__slug=slug).count(), 2)
            self.assertEqual(
                set(
                    ClubMembership.objects.filter(club__slug=slug).values_list(
                        "role",
                        flat=True,
                    )
                ),
                {
                    ClubMembership.Role.OWNER,
                    ClubMembership.Role.MANAGER,
                    ClubMembership.Role.STAFF,
                },
            )

        club_a = Club.objects.get(slug="demo-football-club")
        club_b = Club.objects.get(slug="demo-restricted-club")
        self.assertTrue(club_a.manager_can_settle_transactions)
        self.assertTrue(club_a.manager_can_change_pricing)
        self.assertFalse(club_b.manager_can_settle_transactions)
        self.assertFalse(club_b.manager_can_change_pricing)
        self.assertEqual(club_a.governorate, "ASSIUT")
        self.assertEqual(club_a.city, "ASSIUT_MARKAZ")
        self.assertEqual(club_b.governorate, "SOHAG")
        self.assertEqual(club_b.city, "SOHAG_MARKAZ")
        for club in Club.objects.filter(slug__in=DEMO_CLUB_SLUGS):
            self.assertTrue(is_valid_city_for_governorate(club.governorate, club.city))

        for username in ("staff_a", "staff_b", "staff_c"):
            self.assertEqual(
                ClubMembership.objects.filter(
                    user__username=username,
                    role=ClubMembership.Role.STAFF,
                    is_active=True,
                ).count(),
                1,
            )
        for username in ("manager_a", "manager_b", "manager_c"):
            self.assertEqual(
                ClubMembership.objects.filter(
                    user__username=username,
                    role=ClubMembership.Role.MANAGER,
                    is_active=True,
                ).count(),
                1,
            )

        self.assertEqual(
            set(Booking.objects.values_list("status", flat=True)),
            {
                Booking.Status.HOLD,
                Booking.Status.CONFIRMED,
                Booking.Status.COMPLETED,
                Booking.Status.CANCELLED,
                Booking.Status.NO_SHOW,
                Booking.Status.EXPIRED,
            },
        )
        self.assertEqual(Booking.objects.count(), 38)

        duplicate_references = (
            Transaction.objects.exclude(payment_reference="")
            .values("club", "payment_reference")
            .annotate(reference_count=Count("id"))
            .filter(reference_count__gt=1)
        )
        self.assertFalse(duplicate_references.exists())
        self.assertEqual(Transaction.objects.count(), 19)
        self.assertTrue(
            Transaction.objects.filter(
                club=club_a,
                payment_reference="A-DIGITAL-COURT2-001",
                payment_method=Transaction.PaymentMethod.DIGITAL_WALLET,
                court__name="Demo A Court 2",
            ).exists()
        )

        for slug in DEMO_CLUB_SLUGS:
            self.assertTrue(
                Settlement.objects.filter(
                    club__slug=slug,
                    status=Settlement.Status.PENDING,
                ).exists()
            )
            self.assertTrue(
                Settlement.objects.filter(
                    club__slug=slug,
                    status=Settlement.Status.SETTLED,
                ).exists()
            )
        self.assertEqual(Settlement.objects.count(), 6)
        self.assertEqual(SettlementTransaction.objects.count(), 6)
        self.assertEqual(
            Transaction.objects.filter(
                settlement_line__isnull=True,
                payment_reference__in=(
                    "A-UNSETTLED-001",
                    "A-UNSETTLED-002",
                    "B-UNSETTLED-001",
                    "B-UNSETTLED-002",
                    "C-UNSETTLED-001",
                    "C-UNSETTLED-002",
                ),
            ).count(),
            6,
        )

        for slug in DEMO_CLUB_SLUGS:
            self.assertEqual(AuditLog.objects.filter(club__slug=slug).count(), 5)
        self.assertEqual(AuditLog.objects.count(), 15)

        staff_a_membership = ClubMembership.objects.get(
            user__username="staff_a",
            role=ClubMembership.Role.STAFF,
            is_active=True,
        )
        self.assertEqual(staff_a_membership.court.name, "Demo A Court 1")
        self.assertTrue(
            Booking.objects.filter(
                club=club_a,
                court__name="Demo A Court 2",
            ).exists()
        )

    def test_seed_demo_data_is_idempotent(self):
        self.run_seed_command()
        counts = {
            "users": User.objects.count(),
            "clubs": Club.objects.count(),
            "courts": Court.objects.count(),
            "memberships": ClubMembership.objects.count(),
            "bookings": Booking.objects.count(),
            "transactions": Transaction.objects.count(),
            "settlements": Settlement.objects.count(),
            "lines": SettlementTransaction.objects.count(),
            "audit_logs": AuditLog.objects.count(),
        }

        self.run_seed_command()

        self.assertEqual(User.objects.count(), counts["users"])
        self.assertEqual(Club.objects.count(), counts["clubs"])
        self.assertEqual(Court.objects.count(), counts["courts"])
        self.assertEqual(ClubMembership.objects.count(), counts["memberships"])
        self.assertEqual(Booking.objects.count(), counts["bookings"])
        self.assertEqual(Transaction.objects.count(), counts["transactions"])
        self.assertEqual(Settlement.objects.count(), counts["settlements"])
        self.assertEqual(SettlementTransaction.objects.count(), counts["lines"])
        self.assertEqual(AuditLog.objects.count(), counts["audit_logs"])
