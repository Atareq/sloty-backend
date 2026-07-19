from datetime import time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

DEMO_PASSWORD = "test-pass-123"

USER_SPECS = (
    ("platform_admin", "Platform", "Admin", True, True, True),
    ("owner_a", "Demo A", "Owner", False, False, False),
    ("manager_a", "Demo A", "Manager", False, False, False),
    ("staff_a", "Demo A", "Staff", False, False, False),
    ("owner_b", "Demo B", "Owner", False, False, False),
    ("manager_b", "Demo B", "Manager", False, False, False),
    ("staff_b", "Demo B", "Staff", False, False, False),
    ("owner_c", "Demo C", "Owner", False, False, False),
    ("manager_c", "Demo C", "Manager", False, False, False),
    ("staff_c", "Demo C", "Staff", False, False, False),
)

CLUB_SPECS = (
    {
        "key": "A",
        "slug": "demo-football-club",
        "name": "Demo Football Club",
        "governorate": "ASSIUT",
        "city": "ASSIUT_MARKAZ",
        "address": "Demo detailed address, Assiut",
        "manager_can_settle_transactions": True,
        "manager_can_change_pricing": True,
    },
    {
        "key": "B",
        "slug": "demo-restricted-club",
        "name": "Demo Restricted Club",
        "governorate": "SOHAG",
        "city": "SOHAG_MARKAZ",
        "address": "Restricted demo detailed address, Sohag",
        "manager_can_settle_transactions": False,
        "manager_can_change_pricing": False,
    },
    {
        "key": "C",
        "slug": "demo-other-club",
        "name": "Demo Other Club",
        "governorate": "MINYA",
        "city": "MINYA_MARKAZ",
        "address": "Other demo detailed address, Minya",
        "manager_can_settle_transactions": True,
        "manager_can_change_pricing": False,
    },
)

BOOKING_SPECS = (
    ("hold", Booking.Status.HOLD, "Hold Customer", 10),
    ("confirmed", Booking.Status.CONFIRMED, "Confirmed Customer", 11),
    ("completed", Booking.Status.COMPLETED, "Completed Customer", 12),
    ("cancelled", Booking.Status.CANCELLED, "Cancelled Customer", 13),
    ("no_show", Booking.Status.NO_SHOW, "No Show Customer", 14),
    ("expired", Booking.Status.EXPIRED, "Expired Customer", 15),
    ("partial", Booking.Status.CONFIRMED, "Partial Payment Customer", 16),
    ("full", Booking.Status.CONFIRMED, "Full Payment Customer", 17),
    ("unsettled_1", Booking.Status.CONFIRMED, "Unsettled Customer 1", 18),
    ("unsettled_2", Booking.Status.CONFIRMED, "Unsettled Customer 2", 19),
    ("pending", Booking.Status.CONFIRMED, "Pending Settlement Customer", 20),
    ("settled", Booking.Status.CONFIRMED, "Already Settled Customer", 21),
)

TRANSACTION_SPECS = (
    ("partial", Decimal("100.00"), "PARTIAL-001", Transaction.PaymentMethod.CASH),
    ("full", None, "FULL-001", Transaction.PaymentMethod.CASH),
    ("unsettled_1", Decimal("75.00"), "UNSETTLED-001", Transaction.PaymentMethod.CASH),
    ("unsettled_2", Decimal("125.00"), "UNSETTLED-002", Transaction.PaymentMethod.CASH),
    (
        "pending",
        Decimal("90.00"),
        "PENDING-SETTLEMENT-001",
        Transaction.PaymentMethod.CASH,
    ),
    (
        "settled",
        Decimal("110.00"),
        "ALREADY-SETTLED-001",
        Transaction.PaymentMethod.CASH,
    ),
)

AUDIT_ACTION_SPECS = (
    (AuditLog.Action.BOOKING_CREATED, "Booking", "hold", "staff"),
    (AuditLog.Action.BOOKING_CANCELLED, "Booking", "cancelled", "manager"),
    (AuditLog.Action.TRANSACTION_CREATED, "Transaction", "partial", "staff"),
    (AuditLog.Action.SETTLEMENT_CREATED, "Settlement", "pending", "owner"),
    (AuditLog.Action.SETTLEMENT_MARKED_SETTLED, "Settlement", "settled", "platform"),
)


class Command(BaseCommand):
    help = "Create idempotent local/demo data for manual API testing."

    def handle(self, *args, **options):
        users = self.create_users()
        clubs = self.create_clubs(users)
        courts = self.create_courts(users, clubs)
        self.create_working_hours(courts)
        memberships = self.create_memberships(users, clubs, courts)
        bookings = self.create_bookings(users, clubs, courts)
        transactions = self.create_transactions(users, bookings)
        settlements = self.create_settlements(users, clubs, courts, transactions)
        audit_logs = self.create_audit_logs(
            users,
            clubs,
            courts,
            bookings,
            transactions,
            settlements,
        )
        self.print_summary(
            users,
            clubs,
            courts,
            memberships,
            bookings,
            transactions,
            settlements,
            audit_logs,
        )

    def create_users(self):
        users = {}
        for (
            username,
            first_name,
            last_name,
            is_platform_admin,
            is_staff,
            is_superuser,
        ) in USER_SPECS:
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.com",
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                    "is_platform_admin": is_platform_admin,
                    "is_staff": is_staff,
                    "is_superuser": is_superuser,
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
            users[username] = user
        return users

    def create_clubs(self, users):
        clubs = {}
        for spec in CLUB_SPECS:
            club, _ = Club.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "governorate": spec["governorate"],
                    "city": spec["city"],
                    "address": spec["address"],
                    "is_active": True,
                    "manager_can_settle_transactions": spec[
                        "manager_can_settle_transactions"
                    ],
                    "manager_can_change_pricing": spec["manager_can_change_pricing"],
                    "created_by": users["platform_admin"],
                },
            )
            clubs[spec["key"]] = club
        return clubs

    def create_courts(self, users, clubs):
        courts = {}
        for club_key, club in clubs.items():
            courts[club_key] = {}
            for index, price in ((1, Decimal("300.00")), (2, Decimal("400.00"))):
                court, _ = Court.objects.update_or_create(
                    club=club,
                    name=f"Demo {club_key} Court {index}",
                    defaults={
                        "sport_type": Court.SportType.FOOTBALL,
                        "default_price": price,
                        "slot_duration_minutes": 60,
                        "is_active": True,
                        "requires_digital_payment_reference": index == 2,
                        "internal_hold_expiry_hours": 12,
                        "created_by": users["platform_admin"],
                    },
                )
                courts[club_key][index] = court
        return courts

    def create_working_hours(self, courts):
        for club_courts in courts.values():
            for court in club_courts.values():
                for weekday in CourtWorkingHour.Weekday.values:
                    CourtWorkingHour.objects.update_or_create(
                        court=court,
                        weekday=weekday,
                        defaults={
                            "opens_at": time(8, 0),
                            "closes_at": time(23, 0),
                            "is_closed": False,
                        },
                    )

    def create_memberships(self, users, clubs, courts):
        memberships = {}
        for club_key, club in clubs.items():
            suffix = club_key.lower()
            specs = (
                (
                    f"owner_{suffix}",
                    ClubMembership.Role.OWNER,
                    None,
                ),
                (
                    f"manager_{suffix}",
                    ClubMembership.Role.MANAGER,
                    None,
                ),
                (
                    f"staff_{suffix}",
                    ClubMembership.Role.STAFF,
                    courts[club_key][1],
                ),
            )
            for username, role, court in specs:
                membership, _ = ClubMembership.objects.update_or_create(
                    club=club,
                    user=users[username],
                    role=role,
                    defaults={
                        "court": court,
                        "is_active": True,
                        "created_by": users["platform_admin"],
                    },
                )
                memberships[(club_key, role)] = membership
        return memberships

    def base_day(self):
        return timezone.localdate() + timedelta(days=7)

    def demo_time(self, *, day_offset=0, hour=0):
        naive = timezone.datetime.combine(
            self.base_day() + timedelta(days=day_offset),
            time(hour, 0),
        )
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def create_bookings(self, users, clubs, courts):
        bookings = {}
        for club_index, (club_key, club) in enumerate(clubs.items()):
            for booking_index, (booking_key, status, customer_label, hour) in enumerate(
                BOOKING_SPECS,
                start=1,
            ):
                start_time = self.demo_time(day_offset=club_index, hour=hour)
                court = courts[club_key][1]
                phone = f"+2010{club_index + 1}{booking_index:07d}"
                booking, _ = Booking.objects.update_or_create(
                    club=club,
                    court=court,
                    customer_phone=phone,
                    start_time=start_time,
                    defaults={
                        "customer_name": f"Demo {club_key} {customer_label}",
                        "end_time": start_time + timedelta(minutes=60),
                        "total_price": court.default_price,
                        "status": status,
                        "source": Booking.Source.MANUAL,
                        "notes": "Demo seed booking.",
                        "created_by": users[f"staff_{club_key.lower()}"],
                    },
                )
                bookings[(club_key, booking_key)] = booking

            if club_key == "A":
                for index, hour in ((13, 10), (14, 11)):
                    status = (
                        Booking.Status.HOLD if index == 13 else Booking.Status.CONFIRMED
                    )
                    start_time = self.demo_time(day_offset=1, hour=hour)
                    court = courts[club_key][2]
                    phone = f"+2010{club_index + 1}{index:07d}"
                    booking, _ = Booking.objects.update_or_create(
                        club=club,
                        court=court,
                        customer_phone=phone,
                        start_time=start_time,
                        defaults={
                            "customer_name": (
                                "Demo A Court 2 Hold Customer"
                                if index == 13
                                else "Demo A Court 2 Confirmed Customer"
                            ),
                            "end_time": start_time + timedelta(minutes=60),
                            "total_price": court.default_price,
                            "status": status,
                            "source": Booking.Source.MANUAL,
                            "notes": "Demo seed booking.",
                            "created_by": users["manager_a"],
                        },
                    )
                    bookings[(club_key, f"court2_{index}")] = booking
        return bookings

    def create_transactions(self, users, bookings):
        transactions = {}
        for club_key in ("A", "B", "C"):
            staff_user = users[f"staff_{club_key.lower()}"]
            created_at = self.demo_time(
                day_offset=3 + ord(club_key) - ord("A"),
                hour=9,
            )
            for index, (
                booking_key,
                amount,
                reference_suffix,
                payment_method,
            ) in enumerate(TRANSACTION_SPECS):
                booking = bookings[(club_key, booking_key)]
                payment_reference = f"{club_key}-{reference_suffix}"
                transaction_obj, _ = Transaction.objects.update_or_create(
                    club=booking.club,
                    payment_reference=payment_reference,
                    defaults={
                        "court": booking.court,
                        "booking": booking,
                        "amount": amount or booking.total_price,
                        "payment_method": payment_method,
                        "notes": "Demo seed transaction.",
                        "created_by": staff_user,
                    },
                )
                Transaction.objects.filter(pk=transaction_obj.pk).update(
                    created=created_at + timedelta(minutes=index),
                    modified=created_at + timedelta(minutes=index),
                )
                transaction_obj.refresh_from_db()
                transactions[(club_key, booking_key)] = transaction_obj

            if club_key == "A":
                booking = bookings[("A", "court2_14")]
                transaction_obj, _ = Transaction.objects.update_or_create(
                    club=booking.club,
                    payment_reference="A-DIGITAL-COURT2-001",
                    defaults={
                        "court": booking.court,
                        "booking": booking,
                        "amount": Decimal("150.00"),
                        "payment_method": Transaction.PaymentMethod.DIGITAL_WALLET,
                        "notes": "Demo seed digital transaction for Court 2.",
                        "created_by": staff_user,
                    },
                )
                Transaction.objects.filter(pk=transaction_obj.pk).update(
                    created=created_at + timedelta(minutes=20),
                    modified=created_at + timedelta(minutes=20),
                )
                transaction_obj.refresh_from_db()
                transactions[("A", "court2_digital")] = transaction_obj
        return transactions

    def create_settlements(self, users, clubs, courts, transactions):
        settlements = {}
        for club_key, club in clubs.items():
            owner = users[f"owner_{club_key.lower()}"]
            period_start = self.demo_time(
                day_offset=3 + ord(club_key) - ord("A"),
                hour=8,
            )
            period_end = period_start + timedelta(hours=2)

            pending_transaction = transactions[(club_key, "pending")]
            pending_settlement, _ = Settlement.objects.update_or_create(
                club=club,
                period_start=period_start,
                period_end=period_end,
                status=Settlement.Status.PENDING,
                defaults={
                    "court": courts[club_key][1],
                    "collected_by": pending_transaction.created_by,
                    "total_amount": pending_transaction.amount,
                    "transaction_count": 1,
                    "notes": "Demo pending settlement.",
                    "created_by": owner,
                    "settled_by": None,
                    "settled_at": None,
                },
            )
            SettlementTransaction.objects.update_or_create(
                transaction=pending_transaction,
                defaults={
                    "settlement": pending_settlement,
                    "amount": pending_transaction.amount,
                },
            )

            settled_transaction = transactions[(club_key, "settled")]
            settled_settlement, _ = Settlement.objects.update_or_create(
                club=club,
                period_start=period_start,
                period_end=period_end,
                status=Settlement.Status.SETTLED,
                defaults={
                    "court": courts[club_key][1],
                    "collected_by": settled_transaction.created_by,
                    "total_amount": settled_transaction.amount,
                    "transaction_count": 1,
                    "notes": "Demo settled settlement.",
                    "created_by": owner,
                    "settled_by": users["platform_admin"],
                    "settled_at": period_end + timedelta(minutes=30),
                },
            )
            SettlementTransaction.objects.update_or_create(
                transaction=settled_transaction,
                defaults={
                    "settlement": settled_settlement,
                    "amount": settled_transaction.amount,
                },
            )
            settlements[(club_key, "pending")] = pending_settlement
            settlements[(club_key, "settled")] = settled_settlement
        return settlements

    def create_audit_logs(
        self,
        users,
        clubs,
        courts,
        bookings,
        transactions,
        settlements,
    ):
        audit_logs = {}
        for club_key, club in clubs.items():
            for action, entity_type, seed_target, actor_type in AUDIT_ACTION_SPECS:
                entity, before_data, after_data = self.audit_entity_data(
                    action,
                    club_key,
                    seed_target,
                    bookings,
                    transactions,
                    settlements,
                )
                seed_key = f"DEMO-{club_key}-{action.replace('_', '-')}"
                actor = (
                    users["platform_admin"]
                    if actor_type == "platform"
                    else users[f"{actor_type}_{club_key.lower()}"]
                )
                audit_log, _ = AuditLog.objects.get_or_create(
                    club=club,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity.id,
                    defaults={
                        "court": courts[club_key][1],
                        "actor": actor,
                        "before_data": before_data,
                        "after_data": after_data,
                        "metadata": {"seed_key": seed_key},
                    },
                )
                audit_logs[(club_key, action)] = audit_log
        return audit_logs

    def audit_entity_data(
        self,
        action,
        club_key,
        seed_target,
        bookings,
        transactions,
        settlements,
    ):
        if action in {
            AuditLog.Action.BOOKING_CREATED,
            AuditLog.Action.BOOKING_CANCELLED,
        }:
            booking = bookings[(club_key, seed_target)]
            before_data = (
                {"status": Booking.Status.CONFIRMED}
                if action == AuditLog.Action.BOOKING_CANCELLED
                else {}
            )
            after_data = {"booking_id": booking.id, "status": booking.status}
            return booking, before_data, after_data

        if action == AuditLog.Action.TRANSACTION_CREATED:
            transaction_obj = transactions[(club_key, seed_target)]
            return (
                transaction_obj,
                {},
                {
                    "transaction_id": transaction_obj.id,
                    "amount": str(transaction_obj.amount),
                    "payment_reference": transaction_obj.payment_reference,
                },
            )

        settlement = settlements[(club_key, seed_target)]
        if action == AuditLog.Action.SETTLEMENT_MARKED_SETTLED:
            before_data = {"status": Settlement.Status.PENDING}
            after_data = {
                "settlement_id": settlement.id,
                "status": Settlement.Status.SETTLED,
            }
        else:
            before_data = {}
            after_data = {
                "settlement_id": settlement.id,
                "transaction_count": settlement.transaction_count,
            }
        return settlement, before_data, after_data

    def print_summary(
        self,
        users,
        clubs,
        courts,
        memberships,
        bookings,
        transactions,
        settlements,
        audit_logs,
    ):
        self.stdout.write(self.style.SUCCESS("Seeded demo data successfully."))
        self.stdout.write("")
        self.stdout.write("Users:")
        for username in users:
            self.stdout.write(f"- {username} / {DEMO_PASSWORD}")
        self.stdout.write("")
        self.stdout.write("Clubs:")
        for club in clubs.values():
            self.stdout.write(f"- {club.slug}")
        self.stdout.write("")
        self.stdout.write("Useful checks:")
        self.stdout.write("- manager_a can settle in demo-football-club")
        self.stdout.write("- manager_b cannot settle in demo-restricted-club")
        self.stdout.write("- staff_a can access only Demo A Court 1")
        self.stdout.write("- staff_a cannot access Demo A Court 2")
        self.stdout.write("- staff_a cannot access Club B or Club C data")
        self.stdout.write("")
        self.stdout.write("Endpoints:")
        self.stdout.write("- /api/v1/auth/token/")
        self.stdout.write("- /api/v1/me/")
        self.stdout.write("- /api/v1/clubs/")
        self.stdout.write("- /api/v1/clubs/demo-football-club/bookings/")
        self.stdout.write("- /api/v1/clubs/demo-football-club/transactions/")
        self.stdout.write("- /api/v1/clubs/demo-football-club/settlements/preview/")
        self.stdout.write("- /api/v1/clubs/demo-football-club/audit-logs/")
        self.stdout.write("")
        self.stdout.write(
            "Counts: "
            f"users={len(users)}, clubs={len(clubs)}, "
            f"courts={sum(len(club_courts) for club_courts in courts.values())}, "
            f"memberships={len(memberships)}, bookings={len(bookings)}, "
            f"transactions={len(transactions)}, settlements={len(settlements)}, "
            f"audit_logs={len(audit_logs)}"
        )
