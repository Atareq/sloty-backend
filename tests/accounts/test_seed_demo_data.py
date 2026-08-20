from datetime import time
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Count
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.common.egypt_locations import is_valid_city_for_governorate
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

DEMO_CLUB_SLUGS = (
    "barcelona-fc",
    "real-madrid-cf",
    "liverpool-fc",
)


class SeedDemoDataCommandTests(TestCase):
    def run_seed_command(self):
        call_command("seed_demo_data", verbosity=0)

    def run_albaladya_seed_command(self):
        call_command("seed_demo_data", scenario="albaladya-test", verbosity=0)

    def test_seed_demo_data_creates_predictable_demo_records(self):
        self.run_seed_command()

        platform_admin = User.objects.get(username="platform_admin")
        self.assertTrue(platform_admin.is_platform_admin)
        self.assertTrue(platform_admin.is_staff)
        self.assertTrue(platform_admin.is_superuser)
        self.assertEqual(platform_admin.get_full_name(), "Tarek Platform Admin")

        hossam_admin = User.objects.get(username="hossam_admin")
        self.assertTrue(hossam_admin.is_platform_admin)
        self.assertTrue(hossam_admin.is_staff)
        self.assertTrue(hossam_admin.is_superuser)
        self.assertEqual(hossam_admin.get_full_name(), "Hossam Platform Admin")

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

        club_a = Club.objects.get(slug="barcelona-fc")
        club_b = Club.objects.get(slug="real-madrid-cf")
        club_c = Club.objects.get(slug="liverpool-fc")
        self.assertEqual(club_a.name, "Barcelona FC")
        self.assertEqual(club_b.name, "Real Madrid CF")
        self.assertEqual(club_c.name, "Liverpool FC")
        manager_a = ClubMembership.objects.get(
            club=club_a,
            role=ClubMembership.Role.MANAGER,
        )
        manager_b = ClubMembership.objects.get(
            club=club_b,
            role=ClubMembership.Role.MANAGER,
        )
        self.assertTrue(manager_a.manager_can_settle_transactions)
        self.assertTrue(manager_a.manager_can_change_pricing)
        self.assertFalse(manager_b.manager_can_settle_transactions)
        self.assertFalse(manager_b.manager_can_change_pricing)
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
                court__name="La Masia Training Court",
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
        self.assertEqual(staff_a_membership.court.name, "Spotify Camp Nou")
        self.assertTrue(
            Booking.objects.filter(
                club=club_a,
                court__name="La Masia Training Court",
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

    def test_seed_demo_data_leaves_no_active_non_platform_orphans(self):
        self.run_seed_command()

        self.assertFalse(find_orphan_business_users().exists())

    def test_albaladya_scenario_creates_required_deterministic_records(self):
        self.run_albaladya_seed_command()

        users = {
            user.username: user
            for user in User.objects.filter(
                username__in=("admin", "owner", "manager", "staff")
            )
        }
        self.assertEqual(set(users), {"admin", "owner", "manager", "staff"})
        self.assertEqual(
            {username: user.email for username, user in users.items()},
            {
                "admin": "admin@sloty.test",
                "owner": "owner@sloty.test",
                "manager": "manager@sloty.test",
                "staff": "staff@sloty.test",
            },
        )
        for user in users.values():
            self.assertTrue(user.check_password("Admin@123456"))

        self.assertTrue(users["admin"].is_platform_admin)
        self.assertIsNone(users["admin"].created_by)
        self.assertEqual(users["owner"].created_by, users["admin"])
        self.assertEqual(users["manager"].created_by, users["owner"])
        self.assertEqual(users["staff"].created_by, users["owner"])

        club = Club.objects.get(slug="albaladya-test")
        self.assertEqual(club.name, "Albaladya Test")
        self.assertEqual(club.created_by, users["admin"])
        self.assertEqual(club.governorate, "ASSIUT")
        self.assertEqual(club.city, "ASSIUT_MARKAZ")
        self.assertEqual(str(club.phone_number), "+201000000001")
        self.assertTrue(club.is_active)

        court = Court.objects.get(club=club, name="Albaladya Main Court")
        self.assertEqual(court.sport_type, Court.SportType.FOOTBALL)
        self.assertEqual(court.players_count, 10)
        self.assertEqual(court.slot_duration_minutes, 60)
        self.assertTrue(court.is_active)
        self.assertFalse(court.requires_digital_payment_reference)
        self.assertEqual(court.internal_hold_expiry_hours, 12)
        self.assertEqual(court.created_by, users["owner"])

        owner_membership = ClubMembership.objects.get(
            club=club,
            user=users["owner"],
            role=ClubMembership.Role.OWNER,
        )
        manager_membership = ClubMembership.objects.get(
            club=club,
            user=users["manager"],
            role=ClubMembership.Role.MANAGER,
        )
        staff_membership = ClubMembership.objects.get(
            club=club,
            user=users["staff"],
            role=ClubMembership.Role.STAFF,
        )
        self.assertEqual(owner_membership.created_by, users["admin"])
        self.assertEqual(manager_membership.created_by, users["owner"])
        self.assertEqual(staff_membership.created_by, users["owner"])
        self.assertIsNone(owner_membership.court)
        self.assertIsNone(manager_membership.court)
        self.assertEqual(staff_membership.court, court)
        self.assertFalse(owner_membership.manager_can_settle_transactions)
        self.assertFalse(owner_membership.manager_can_change_pricing)
        self.assertTrue(manager_membership.manager_can_settle_transactions)
        self.assertTrue(manager_membership.manager_can_change_pricing)
        self.assertFalse(staff_membership.manager_can_settle_transactions)
        self.assertFalse(staff_membership.manager_can_change_pricing)

        self.assertEqual(CourtWorkingHour.objects.filter(court=court).count(), 7)
        friday = CourtWorkingHour.objects.get(
            court=court,
            weekday=CourtWorkingHour.Weekday.FRIDAY,
        )
        self.assertFalse(friday.pricing_periods.exists())

        open_days = [
            working_hour
            for working_hour in CourtWorkingHour.objects.filter(court=court)
            if working_hour.pricing_periods.exists()
        ]
        self.assertEqual(len(open_days), 6)
        for working_hour in open_days:
            periods = list(working_hour.pricing_periods.order_by("starts_at"))
            self.assertEqual(len(periods), 2)
            self.assertEqual(periods[0].starts_at, time(10, 0))
            self.assertEqual(periods[0].ends_at, time(18, 0))
            self.assertEqual(periods[0].price, Decimal("200.00"))
            self.assertEqual(periods[1].starts_at, time(18, 0))
            self.assertEqual(periods[1].ends_at, time(23, 0))
            self.assertEqual(periods[1].price, Decimal("300.00"))

    def test_albaladya_scenario_is_idempotent_and_repairs_values(self):
        self.run_albaladya_seed_command()
        counts = {
            "users": User.objects.filter(
                username__in=("admin", "owner", "manager", "staff")
            ).count(),
            "clubs": Club.objects.filter(slug="albaladya-test").count(),
            "courts": Court.objects.filter(club__slug="albaladya-test").count(),
            "memberships": ClubMembership.objects.filter(
                club__slug="albaladya-test"
            ).count(),
            "working_hours": CourtWorkingHour.objects.filter(
                court__club__slug="albaladya-test"
            ).count(),
            "pricing_periods": CourtWorkingHourPricePeriod.objects.filter(
                working_hour__court__club__slug="albaladya-test"
            ).count(),
        }

        owner = User.objects.get(username="owner")
        manager_membership = ClubMembership.objects.get(
            club__slug="albaladya-test",
            user__username="manager",
            role=ClubMembership.Role.MANAGER,
        )
        club = Club.objects.get(slug="albaladya-test")
        court = Court.objects.get(club=club, name="Albaladya Main Court")
        owner.email = "wrong@example.com"
        owner.created_by = None
        owner.set_password("wrong-password")
        owner.save(update_fields=["email", "created_by", "password"])
        club.name = "Wrong Club Name"
        club.save(update_fields=["name"])
        court.slot_duration_minutes = 30
        court.save(update_fields=["slot_duration_minutes"])
        manager_membership.manager_can_settle_transactions = False
        manager_membership.manager_can_change_pricing = False
        manager_membership.save(
            update_fields=[
                "manager_can_settle_transactions",
                "manager_can_change_pricing",
            ]
        )

        self.run_albaladya_seed_command()

        self.assertEqual(
            User.objects.filter(
                username__in=("admin", "owner", "manager", "staff")
            ).count(),
            counts["users"],
        )
        self.assertEqual(Club.objects.filter(slug="albaladya-test").count(), 1)
        self.assertEqual(
            Court.objects.filter(club__slug="albaladya-test").count(),
            counts["courts"],
        )
        self.assertEqual(
            ClubMembership.objects.filter(club__slug="albaladya-test").count(),
            counts["memberships"],
        )
        self.assertEqual(
            CourtWorkingHour.objects.filter(court__club__slug="albaladya-test").count(),
            counts["working_hours"],
        )
        self.assertEqual(
            CourtWorkingHourPricePeriod.objects.filter(
                working_hour__court__club__slug="albaladya-test"
            ).count(),
            counts["pricing_periods"],
        )

        owner.refresh_from_db()
        manager_membership.refresh_from_db()
        club.refresh_from_db()
        court.refresh_from_db()
        self.assertEqual(owner.email, "owner@sloty.test")
        self.assertEqual(owner.created_by, User.objects.get(username="admin"))
        self.assertTrue(owner.check_password("Admin@123456"))
        self.assertEqual(club.name, "Albaladya Test")
        self.assertEqual(court.slot_duration_minutes, 60)
        self.assertTrue(manager_membership.manager_can_settle_transactions)
        self.assertTrue(manager_membership.manager_can_change_pricing)
