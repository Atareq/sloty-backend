from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction


class SeedDemoDataCommandTests(TestCase):
    def run_seed_command(self):
        call_command("seed_demo_data", verbosity=0)

    def test_seed_demo_data_creates_one_testing_club_and_related_records(self):
        self.run_seed_command()

        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(Profile.objects.filter(user__isnull=False).count(), 3)
        self.assertEqual(AdminProfile.objects.count(), 1)
        self.assertEqual(OwnerProfile.objects.count(), 1)
        self.assertEqual(StaffProfile.objects.count(), 1)
        self.assertEqual(Club.objects.count(), 1)
        self.assertEqual(Court.objects.count(), 2)
        self.assertEqual(Booking.objects.count(), 14)
        self.assertEqual(Transaction.objects.count(), 6)
        self.assertEqual(Settlement.objects.count(), 2)
        self.assertEqual(SettlementTransaction.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 5)

        admin = User.objects.get(email="platform_admin@example.com")
        owner = User.objects.get(email="owner@example.com")
        staff = User.objects.get(email="staff@example.com")
        club = Club.objects.get(slug="testing")
        main_court = Court.objects.get(club=club, name="Testing Main Court")
        secondary_court = Court.objects.get(club=club, name="Testing Secondary Court")

        self.assertEqual(admin.profile.role, Profile.Role.ADMIN)
        self.assertEqual(owner.profile.role, Profile.Role.OWNER)
        self.assertEqual(staff.profile.role, Profile.Role.STAFF)
        self.assertEqual(club.name, "Testing")
        self.assertEqual(club.created_by, admin.profile)
        self.assertEqual(owner.profile.owner_profile.clubs.get(), club)
        self.assertEqual(staff.profile.staff_profile.club, club)
        self.assertTrue(
            staff.profile.staff_profile.court_accesses.filter(court=main_court).exists()
        )
        self.assertFalse(
            staff.profile.staff_profile.court_accesses.filter(
                court=secondary_court
            ).exists()
        )
        self.assertTrue(
            Booking.objects.filter(
                club=club,
                status__in=Booking.BLOCKING_STATUSES,
            ).exists()
        )
        self.assertTrue(Transaction.objects.filter(club=club).exists())
        self.assertFalse(
            Transaction.objects.filter(
                payment_reference="TESTING-DIGITAL-COURT2-001"
            ).exists()
        )
        self.assertTrue(Settlement.objects.filter(club=club).exists())
        self.assertTrue(
            SettlementTransaction.objects.filter(settlement__club=club).exists()
        )

    def test_seed_demo_data_is_idempotent(self):
        self.run_seed_command()
        counts = {
            "users": User.objects.count(),
            "profiles": Profile.objects.count(),
            "clubs": Club.objects.count(),
            "courts": Court.objects.count(),
            "bookings": Booking.objects.count(),
            "transactions": Transaction.objects.count(),
            "settlements": Settlement.objects.count(),
            "lines": SettlementTransaction.objects.count(),
            "audit_logs": AuditLog.objects.count(),
        }

        self.run_seed_command()

        models = {
            "users": User,
            "profiles": Profile,
            "clubs": Club,
            "courts": Court,
            "bookings": Booking,
            "transactions": Transaction,
            "settlements": Settlement,
            "lines": SettlementTransaction,
            "audit_logs": AuditLog,
        }
        for model_name, expected_count in counts.items():
            self.assertEqual(models[model_name].objects.count(), expected_count)

    def test_seed_demo_data_leaves_no_active_non_platform_orphans(self):
        self.run_seed_command()

        self.assertFalse(find_orphan_business_users().exists())
