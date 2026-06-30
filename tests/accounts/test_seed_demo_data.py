from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.transactions.models import Transaction


class SeedDemoDataCommandTests(TestCase):
    def run_seed_command(self):
        call_command("seed_demo_data", verbosity=0)

    def test_seed_demo_data_creates_predictable_demo_records(self):
        self.run_seed_command()

        self.assertTrue(User.objects.filter(username="platform_admin").exists())
        self.assertTrue(User.objects.filter(username="owner_user").exists())
        self.assertTrue(User.objects.filter(username="manager_user").exists())
        self.assertTrue(User.objects.filter(username="staff_user").exists())
        self.assertTrue(Club.objects.filter(slug="demo-football-club").exists())
        self.assertEqual(
            Court.objects.filter(club__slug="demo-football-club").count(), 2
        )
        self.assertEqual(
            ClubMembership.objects.filter(club__slug="demo-football-club").count(),
            3,
        )
        self.assertEqual(
            Booking.objects.filter(club__slug="demo-football-club").count(), 7
        )
        self.assertEqual(
            set(
                Booking.objects.filter(club__slug="demo-football-club").values_list(
                    "status",
                    flat=True,
                )
            ),
            {
                Booking.Status.HOLD,
                Booking.Status.CONFIRMED,
                Booking.Status.COMPLETED,
                Booking.Status.CANCELLED,
                Booking.Status.NO_SHOW,
                Booking.Status.EXPIRED,
            },
        )
        self.assertEqual(
            Transaction.objects.filter(club__slug="demo-football-club").count(),
            2,
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
        }

        self.run_seed_command()

        self.assertEqual(User.objects.count(), counts["users"])
        self.assertEqual(Club.objects.count(), counts["clubs"])
        self.assertEqual(Court.objects.count(), counts["courts"])
        self.assertEqual(ClubMembership.objects.count(), counts["memberships"])
        self.assertEqual(Booking.objects.count(), counts["bookings"])
        self.assertEqual(Transaction.objects.count(), counts["transactions"])
