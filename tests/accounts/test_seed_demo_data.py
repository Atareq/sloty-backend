from datetime import time
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.common.egypt_locations import is_valid_city_for_governorate
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile
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
        self.assertEqual(platform_admin.profile.role, Profile.Role.ADMIN)
        self.assertTrue(AdminProfile.objects.filter(user=platform_admin).exists())
        self.assertTrue(platform_admin.is_staff)
        self.assertTrue(platform_admin.is_superuser)
        self.assertEqual(platform_admin.get_full_name(), "Tarek Platform Admin")

        hossam_admin = User.objects.get(username="hossam_admin")
        self.assertEqual(hossam_admin.profile.role, Profile.Role.ADMIN)
        self.assertTrue(AdminProfile.objects.filter(user=hossam_admin).exists())
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
            club = Club.objects.get(slug=slug)
            self.assertTrue(OwnerProfile.objects.filter(clubs=club).exists())
            self.assertTrue(StaffProfile.objects.filter(club=club).exists())

        club_a = Club.objects.get(slug="barcelona-fc")
        club_b = Club.objects.get(slug="real-madrid-cf")
        club_c = Club.objects.get(slug="liverpool-fc")
        self.assertEqual(club_a.name, "Barcelona FC")
        self.assertEqual(club_b.name, "Real Madrid CF")
        self.assertEqual(club_c.name, "Liverpool FC")
        self.assertEqual(club_a.governorate, "ASSIUT")
        self.assertEqual(club_a.city, "ASSIUT_MARKAZ")
        self.assertEqual(club_b.governorate, "SOHAG")
        self.assertEqual(club_b.city, "SOHAG_MARKAZ")
        for club in Club.objects.filter(slug__in=DEMO_CLUB_SLUGS):
            self.assertTrue(is_valid_city_for_governorate(club.governorate, club.city))

        for username in ("staff_a", "staff_b", "staff_c"):
            staff_user = User.objects.get(username=username)
            self.assertEqual(staff_user.profile.role, Profile.Role.STAFF)
            self.assertTrue(staff_user.is_active)
            self.assertTrue(hasattr(staff_user.profile, "staff_profile"))

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
        self.assertTrue(Transaction.objects.exists())
        self.assertEqual(Settlement.objects.count(), 6)
        self.assertTrue(SettlementTransaction.objects.exists())
        self.assertTrue(Settlement.objects.filter(settled_by=platform_admin).exists())

        for slug in DEMO_CLUB_SLUGS:
            self.assertEqual(AuditLog.objects.filter(club__slug=slug).count(), 5)
        self.assertEqual(AuditLog.objects.count(), 15)

        staff_a = User.objects.get(username="staff_a")
        self.assertEqual(staff_a.profile.staff_profile.club, club_a)
        self.assertTrue(
            staff_a.profile.staff_profile.court_accesses.filter(
                court__name="Spotify Camp Nou"
            ).exists()
        )
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
            "profiles": Profile.objects.count(),
            "owner_profiles": OwnerProfile.objects.count(),
            "staff_profiles": StaffProfile.objects.count(),
            "admin_profiles": AdminProfile.objects.count(),
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
        self.assertEqual(Profile.objects.count(), counts["profiles"])
        self.assertEqual(OwnerProfile.objects.count(), counts["owner_profiles"])
        self.assertEqual(StaffProfile.objects.count(), counts["staff_profiles"])
        self.assertEqual(AdminProfile.objects.count(), counts["admin_profiles"])
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
            for user in User.objects.filter(username__in=("admin", "owner", "staff"))
        }
        self.assertEqual(set(users), {"admin", "owner", "staff"})
        self.assertEqual(
            {username: user.email for username, user in users.items()},
            {
                "admin": "admin@sloty.test",
                "owner": "owner@sloty.test",
                "staff": "staff@sloty.test",
            },
        )
        for user in users.values():
            self.assertTrue(user.check_password("Admin@123456"))

        admin = users["admin"]
        owner = users["owner"]
        staff = users["staff"]

        self.assertEqual(admin.profile.role, Profile.Role.ADMIN)
        self.assertTrue(AdminProfile.objects.filter(user=admin).exists())
        self.assertIsNone(admin.profile.created_by)

        self.assertEqual(owner.profile.created_by, admin.profile)
        self.assertEqual(owner.profile.role, Profile.Role.OWNER)
        self.assertTrue(hasattr(owner.profile, "owner_profile"))

        self.assertEqual(staff.profile.created_by, owner.profile)
        self.assertEqual(staff.profile.role, Profile.Role.STAFF)
        self.assertTrue(hasattr(staff.profile, "staff_profile"))

        club = Club.objects.get(slug="albaladya-test")
        self.assertEqual(club.name, "Albaladya Test")
        self.assertEqual(club.created_by, admin.profile)
        self.assertEqual(club.governorate, "ASSIUT")
        self.assertEqual(club.city, "ASSIUT_MARKAZ")
        self.assertEqual(str(club.phone_number), "+201000000001")
        self.assertTrue(club.is_active)
        self.assertTrue(owner.profile.owner_profile.clubs.filter(pk=club.pk).exists())

        court = Court.objects.get(club=club, name="Albaladya Main Court")
        self.assertEqual(court.sport_type, Court.SportType.FOOTBALL)
        self.assertEqual(court.players_count, 10)
        self.assertEqual(court.slot_duration_minutes, 60)
        self.assertTrue(court.is_active)
        self.assertFalse(court.requires_digital_payment_reference)
        self.assertEqual(court.internal_hold_expiry_hours, 12)
        self.assertEqual(court.created_by, owner.profile)
        self.assertEqual(staff.profile.staff_profile.club, club)
        self.assertTrue(
            staff.profile.staff_profile.court_accesses.filter(court=court).exists()
        )

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
                username__in=("admin", "owner", "staff")
            ).count(),
            "clubs": Club.objects.filter(slug="albaladya-test").count(),
            "courts": Court.objects.filter(club__slug="albaladya-test").count(),
            "profiles": Profile.objects.filter(
                user__username__in=("admin", "owner", "staff")
            ).count(),
            "working_hours": CourtWorkingHour.objects.filter(
                court__club__slug="albaladya-test"
            ).count(),
            "pricing_periods": CourtWorkingHourPricePeriod.objects.filter(
                working_hour__court__club__slug="albaladya-test"
            ).count(),
        }

        owner = User.objects.get(username="owner")
        club = Club.objects.get(slug="albaladya-test")
        court = Court.objects.get(club=club, name="Albaladya Main Court")
        owner.email = "wrong@example.com"
        owner.set_password("wrong-password")
        owner.save(update_fields=["email", "password"])
        owner.profile.created_by = None
        owner.profile.save(update_fields=["created_by"])
        club.name = "Wrong Club Name"
        club.save(update_fields=["name"])
        court.slot_duration_minutes = 30
        court.save(update_fields=["slot_duration_minutes"])

        self.run_albaladya_seed_command()

        self.assertEqual(
            User.objects.filter(username__in=("admin", "owner", "staff")).count(),
            counts["users"],
        )
        self.assertEqual(Club.objects.filter(slug="albaladya-test").count(), 1)
        self.assertEqual(
            Court.objects.filter(club__slug="albaladya-test").count(),
            counts["courts"],
        )
        self.assertEqual(
            Profile.objects.filter(
                user__username__in=("admin", "owner", "staff")
            ).count(),
            counts["profiles"],
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
        owner.profile.refresh_from_db()
        club.refresh_from_db()
        court.refresh_from_db()
        self.assertEqual(owner.email, "owner@sloty.test")
        self.assertEqual(
            owner.profile.created_by,
            User.objects.get(username="admin").profile,
        )
        self.assertTrue(owner.check_password("Admin@123456"))
        self.assertEqual(club.name, "Albaladya Test")
        self.assertEqual(court.slot_duration_minutes, 60)
