from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

DEMO_PASSWORD = "test-pass-123"


class Command(BaseCommand):
    help = "Create idempotent local/demo data for manual API testing."

    def handle(self, *args, **options):
        users = self.create_users()
        club = self.create_club()
        courts = self.create_courts(club)
        self.create_memberships(users, club, courts)
        bookings = self.create_bookings(club, courts)
        transactions = self.create_transactions(users, bookings)
        self.create_settlements(users, club, courts, transactions)

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded demo data: users=4, clubs=1, courts=2, "
                f"memberships=3, bookings={len(bookings)}, "
                f"transactions={len(transactions)}, settlements=2"
            )
        )
        self.stdout.write(
            "Demo password for all users: test-pass-123. "
            "Use club_slug=demo-football-club for club-context token claims."
        )

    def create_users(self):
        user_specs = {
            "platform_admin": {
                "email": "platform_admin@example.com",
                "first_name": "Platform",
                "last_name": "Admin",
                "is_platform_admin": True,
            },
            "owner_user": {
                "email": "owner_user@example.com",
                "first_name": "Demo",
                "last_name": "Owner",
                "is_platform_admin": False,
            },
            "manager_user": {
                "email": "manager_user@example.com",
                "first_name": "Demo",
                "last_name": "Manager",
                "is_platform_admin": False,
            },
            "staff_user": {
                "email": "staff_user@example.com",
                "first_name": "Demo",
                "last_name": "Staff",
                "is_platform_admin": False,
            },
        }
        users = {}
        for username, defaults in user_specs.items():
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    **defaults,
                    "is_active": True,
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
            users[username] = user
        return users

    def create_club(self):
        club, _ = Club.objects.update_or_create(
            slug="demo-football-club",
            defaults={
                "name": "Demo Football Club",
                "city": "Assiut",
                "area": "Demo Area",
                "is_active": True,
                "manager_can_settle_transactions": True,
            },
        )
        return club

    def create_courts(self, club):
        courts = {}
        for name in ("Demo Court 1", "Demo Court 2"):
            court, _ = Court.objects.update_or_create(
                club=club,
                name=name,
                defaults={
                    "default_price": Decimal("300.00"),
                    "slot_duration_minutes": 60,
                    "is_active": True,
                },
            )
            courts[name] = court
        return courts

    def create_memberships(self, users, club, courts):
        membership_specs = (
            ("owner_user", ClubMembership.Role.OWNER, None),
            ("manager_user", ClubMembership.Role.MANAGER, None),
            ("staff_user", ClubMembership.Role.STAFF, courts["Demo Court 1"]),
        )
        for username, role, court in membership_specs:
            ClubMembership.objects.update_or_create(
                club=club,
                user=users[username],
                role=role,
                defaults={
                    "court": court,
                    "is_active": True,
                    "created_by": users["platform_admin"],
                },
            )

    def demo_time(self, hour):
        day_start = timezone.make_aware(
            timezone.datetime(2026, 7, 1, 0, 0),
            timezone.get_current_timezone(),
        )
        return day_start + timedelta(hours=hour)

    def create_bookings(self, club, courts):
        specs = (
            (Booking.Status.HOLD, "Demo Hold Customer", "+201000000101", 18),
            (Booking.Status.CONFIRMED, "Demo Confirmed Customer", "+201000000102", 19),
            (Booking.Status.COMPLETED, "Demo Completed Customer", "+201000000103", 20),
            (Booking.Status.CANCELLED, "Demo Cancelled Customer", "+201000000104", 21),
            (Booking.Status.NO_SHOW, "Demo No Show Customer", "+201000000105", 22),
            (Booking.Status.EXPIRED, "Demo Expired Customer", "+201000000106", 23),
            (Booking.Status.CONFIRMED, "Demo Fully Paid Customer", "+201000000107", 17),
            (
                Booking.Status.CONFIRMED,
                "Demo Unsettled Customer 1",
                "+201000000108",
                24,
            ),
            (
                Booking.Status.CONFIRMED,
                "Demo Unsettled Customer 2",
                "+201000000109",
                25,
            ),
            (
                Booking.Status.CONFIRMED,
                "Demo Pending Settlement Customer",
                "+201000000110",
                26,
            ),
            (
                Booking.Status.CONFIRMED,
                "Demo Settled Customer",
                "+201000000111",
                27,
            ),
        )
        bookings = {}
        for status, customer_name, customer_phone, hour in specs:
            start_time = self.demo_time(hour)
            booking, _ = Booking.objects.update_or_create(
                club=club,
                court=courts["Demo Court 1"],
                customer_phone=customer_phone,
                start_time=start_time,
                defaults={
                    "customer_name": customer_name,
                    "end_time": self.demo_time(hour + 1),
                    "total_price": Decimal("300.00"),
                    "status": status,
                    "source": Booking.Source.MANUAL,
                    "notes": "Demo seed booking.",
                },
            )
            bookings[customer_phone] = booking
        return bookings

    def create_transactions(self, users, bookings):
        transaction_specs = (
            ("+201000000102", Decimal("100.00"), "DEMO-PARTIAL-001"),
            ("+201000000107", Decimal("300.00"), "DEMO-FULL-001"),
            ("+201000000108", Decimal("75.00"), "DEMO-UNSETTLED-001"),
            ("+201000000109", Decimal("125.00"), "DEMO-UNSETTLED-002"),
            ("+201000000110", Decimal("90.00"), "DEMO-PENDING-SETTLED-001"),
            ("+201000000111", Decimal("110.00"), "DEMO-ALREADY-SETTLED-001"),
        )
        transactions = {}
        for customer_phone, amount, reference in transaction_specs:
            booking = bookings[customer_phone]
            transaction_obj, _ = Transaction.objects.update_or_create(
                club=booking.club,
                payment_reference=reference,
                defaults={
                    "court": booking.court,
                    "booking": booking,
                    "amount": amount,
                    "payment_method": Transaction.PaymentMethod.CASH,
                    "notes": "Demo seed transaction.",
                    "created_by": users["staff_user"],
                },
            )
            transactions[reference] = transaction_obj
        return transactions

    def create_settlements(self, users, club, courts, transactions):
        pending_settlement, _ = Settlement.objects.update_or_create(
            club=club,
            period_start=self.demo_time(26),
            period_end=self.demo_time(27),
            defaults={
                "court": courts["Demo Court 1"],
                "status": Settlement.Status.PENDING,
                "total_amount": Decimal("90.00"),
                "transaction_count": 1,
                "notes": "Demo pending settlement for mark-settled testing.",
                "created_by": users["owner_user"],
                "settled_by": None,
                "settled_at": None,
            },
        )
        SettlementTransaction.objects.update_or_create(
            transaction=transactions["DEMO-PENDING-SETTLED-001"],
            defaults={
                "settlement": pending_settlement,
                "amount": transactions["DEMO-PENDING-SETTLED-001"].amount,
            },
        )

        settled_settlement, _ = Settlement.objects.update_or_create(
            club=club,
            period_start=self.demo_time(27),
            period_end=self.demo_time(28),
            defaults={
                "court": courts["Demo Court 1"],
                "status": Settlement.Status.SETTLED,
                "total_amount": Decimal("110.00"),
                "transaction_count": 1,
                "notes": "Demo settled settlement for list/detail/filter testing.",
                "created_by": users["owner_user"],
                "settled_by": users["platform_admin"],
                "settled_at": self.demo_time(29),
            },
        )
        SettlementTransaction.objects.update_or_create(
            transaction=transactions["DEMO-ALREADY-SETTLED-001"],
            defaults={
                "settlement": settled_settlement,
                "amount": transactions["DEMO-ALREADY-SETTLED-001"].amount,
            },
        )
