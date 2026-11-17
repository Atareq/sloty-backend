from datetime import time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking
from apps.bookings.services import hold_expiry_deadline, resolve_booking_club_player
from apps.clubs.models import Club
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

DEMO_PASSWORD = "123"
USER_SPECS = (
    {
        "key": "platform_admin",
        "phone_number": "+201011111111",
        "email": "platform_admin@example.com",
        "first_name": "Tarek",
        "last_name": "Platform Admin",
        "role": Profile.Role.ADMIN,
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "key": "owner",
        "phone_number": "+201022222221",
        "email": "owner@example.com",
        "first_name": "Owner",
        "last_name": "Testing",
        "role": Profile.Role.OWNER,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "key": "staff",
        "phone_number": "+201033333331",
        "email": "staff@example.com",
        "first_name": "Staff",
        "last_name": "Testing",
        "role": Profile.Role.STAFF,
        "is_staff": False,
        "is_superuser": False,
    },
)

CLUB_SPECS = (
    {
        "key": "testing",
        "slug": "testing",
        "name": "Testing",
        "governorate": "ASSIUT",
        "city": "ASSIUT_MARKAZ",
        "address": "Testing club, Assiut",
        "court_names": ("Testing Main Court", "Testing Secondary Court"),
    },
)
CLUB_SPECS_BY_KEY = {spec["key"]: spec for spec in CLUB_SPECS}

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
    (AuditLog.Action.BOOKING_CANCELLED, "Booking", "cancelled", "owner"),
    (AuditLog.Action.TRANSACTION_CREATED, "Transaction", "partial", "staff"),
    (AuditLog.Action.SETTLEMENT_CREATED, "Settlement", "pending", "owner"),
    (AuditLog.Action.SETTLEMENT_MARKED_SETTLED, "Settlement", "settled", "platform"),
)


class Command(BaseCommand):
    help = "Create idempotent local/demo data for manual API testing."

    def handle(self, *args, **options):
        # Demo users, including a superuser, share a published password.
        if settings.SETTINGS_MODULE == "config.settings.production":
            raise CommandError("seed_demo_data must not run with production settings.")
        users = self.create_users()
        profiles = self.create_admin_profiles(users)
        clubs = self.create_clubs(profiles)
        courts = self.create_courts(profiles, clubs)
        self.create_working_hours(courts)
        profiles = self.create_profiles(users, clubs, courts, profiles)
        bookings = self.create_bookings(clubs, courts, profiles)
        transactions = self.create_transactions(profiles, bookings)
        settlements = self.create_settlements(profiles, clubs, courts, transactions)
        audit_logs = self.create_audit_logs(
            profiles,
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
            profiles,
            bookings,
            transactions,
            settlements,
            audit_logs,
        )

    def create_users(self):
        users = {}
        for spec in USER_SPECS:
            user, _ = User.objects.update_or_create(
                phone_number=spec["phone_number"],
                defaults={
                    "email": spec["email"],
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "is_active": True,
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
            users[spec["key"]] = user
        return users

    def create_admin_profiles(self, users):
        profiles = {}
        for spec in USER_SPECS:
            if spec["role"] != Profile.Role.ADMIN:
                continue
            profiles[spec["key"]], _ = AdminProfile.objects.update_or_create(
                user=users[spec["key"]],
                defaults={
                    "role": spec["role"],
                    "is_enabled": True,
                    "created_by": None,
                },
            )
        return profiles

    def create_clubs(self, profiles):
        admin_profile = profiles["platform_admin"]
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
                    "created_by": admin_profile,
                },
            )
            clubs[spec["key"]] = club
        return clubs

    def create_courts(self, profiles, clubs):
        admin_profile = profiles["platform_admin"]
        courts = {}
        for club_key, club in clubs.items():
            courts[club_key] = {}
            club_spec = CLUB_SPECS_BY_KEY[club_key]
            for index, price in ((1, Decimal("300.00")), (2, Decimal("400.00"))):
                court, _ = Court.objects.update_or_create(
                    club=club,
                    name=club_spec["court_names"][index - 1],
                    defaults={
                        "sport_type": Court.SportType.FOOTBALL,
                        "default_price": price,
                        "minimum_deposit": price,
                        "slot_duration_minutes": 60,
                        "is_active": True,
                        "requires_digital_payment_reference": index == 2,
                        "internal_hold_expiry_hours": 12,
                        "cancellation_refund_notice_days": 0,
                        "created_by": admin_profile,
                    },
                )
                courts[club_key][index] = court
        return courts

    def create_working_hours(self, courts):
        for club_courts in courts.values():
            for court in club_courts.values():
                for weekday in CourtWorkingHour.Weekday.values:
                    working_hour, _ = CourtWorkingHour.objects.update_or_create(
                        court=court,
                        weekday=weekday,
                        defaults={},
                    )
                    CourtWorkingHourPricePeriod.objects.update_or_create(
                        working_hour=working_hour,
                        starts_at=time(8, 0),
                        defaults={
                            "ends_at": time(23, 0),
                            "price": court.default_price,
                        },
                    )

    def create_profiles(self, users, clubs, courts, admin_profiles):
        profiles = dict(admin_profiles)
        platform_admin = profiles["platform_admin"]
        for club_key, club in clubs.items():
            owner, _ = OwnerProfile.objects.update_or_create(
                user=users["owner"],
                defaults={
                    "role": Profile.Role.OWNER,
                    "is_enabled": True,
                    "created_by": platform_admin,
                },
            )
            owner.clubs.set([club])
            staff, _ = StaffProfile.objects.update_or_create(
                user=users["staff"],
                defaults={
                    "role": Profile.Role.STAFF,
                    "is_enabled": True,
                    "club": club,
                    "created_by": owner,
                },
            )
            StaffCourtAccess.objects.get_or_create(
                staff_profile=staff, court=courts[club_key][1]
            )
            profiles["owner"] = owner
            profiles["staff"] = staff
        return profiles

    def base_day(self):
        return timezone.localdate() + timedelta(days=7)

    def demo_time(self, *, day_offset=0, hour=0):
        naive = timezone.datetime.combine(
            self.base_day() + timedelta(days=day_offset),
            time(hour, 0),
        )
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def create_bookings(self, clubs, courts, profiles):
        bookings = {}
        for club_index, (club_key, club) in enumerate(clubs.items()):
            for booking_index, (booking_key, status, customer_label, hour) in enumerate(
                BOOKING_SPECS,
                start=1,
            ):
                start_time = self.demo_time(day_offset=club_index, hour=hour)
                court = courts[club_key][1]
                phone = f"+2010{club_index + 1}{booking_index:07d}"
                customer_name = f"Demo {club_key} {customer_label}"
                staff_profile = profiles["staff"]
                # Identity resolution: seeded bookings bypass create_booking()
                # (update_or_create is used for idempotent re-seeding), but
                # must still resolve a real ClubPlayer — Booking.club_player
                # is the identity link every other creation path populates.
                club_player = resolve_booking_club_player(
                    club=club,
                    customer_name=customer_name,
                    customer_phone=phone,
                )
                booking, _ = Booking.objects.update_or_create(
                    club=club,
                    court=court,
                    start_time=start_time,
                    defaults={
                        "club_player": club_player,
                        "end_time": start_time + timedelta(minutes=60),
                        "total_price": court.default_price,
                        "status": status,
                        "hold_expires_at": (
                            hold_expiry_deadline(court)
                            if status == Booking.Status.HOLD
                            else None
                        ),
                        "source": Booking.Source.MANUAL,
                        "notes": "Demo seed booking.",
                        "created_by": staff_profile,
                        "last_status_changed_by": staff_profile,
                        "last_status_changed_by_type": (
                            Booking.LastStatusActorType.PROFILE
                        ),
                    },
                )
                bookings[(club_key, booking_key)] = booking

            if club_key == "testing":
                for index, hour in ((13, 10), (14, 11)):
                    status = (
                        Booking.Status.HOLD if index == 13 else Booking.Status.CONFIRMED
                    )
                    start_time = self.demo_time(day_offset=1, hour=hour)
                    court = courts[club_key][2]
                    phone = f"+2010{club_index + 1}{index:07d}"
                    customer_name = (
                        "Demo Testing Court 2 Hold Customer"
                        if index == 13
                        else "Demo Testing Court 2 Confirmed Customer"
                    )
                    club_player = resolve_booking_club_player(
                        club=club,
                        customer_name=customer_name,
                        customer_phone=phone,
                    )
                    booking, _ = Booking.objects.update_or_create(
                        club=club,
                        court=court,
                        start_time=start_time,
                        defaults={
                            "club_player": club_player,
                            "end_time": start_time + timedelta(minutes=60),
                            "total_price": court.default_price,
                            "status": status,
                            "hold_expires_at": (
                                hold_expiry_deadline(court)
                                if status == Booking.Status.HOLD
                                else None
                            ),
                            "source": Booking.Source.MANUAL,
                            "notes": "Demo seed booking.",
                            "created_by": profiles["owner"],
                            "last_status_changed_by": profiles["owner"],
                            "last_status_changed_by_type": (
                                Booking.LastStatusActorType.PROFILE
                            ),
                        },
                    )
                    bookings[(club_key, f"court2_{index}")] = booking
        return bookings

    def create_transactions(self, profiles, bookings):
        transactions = {}
        for club_key in ("testing",):
            staff_profile = profiles["staff"]
            created_at = self.demo_time(day_offset=3, hour=9)
            for index, (
                booking_key,
                amount,
                reference_suffix,
                payment_method,
            ) in enumerate(TRANSACTION_SPECS):
                booking = bookings[(club_key, booking_key)]
                payment_reference = f"{club_key.upper()}-{reference_suffix}"
                transaction_obj, _ = Transaction.objects.update_or_create(
                    club=booking.club,
                    payment_reference=payment_reference,
                    defaults={
                        "court": booking.court,
                        "booking": booking,
                        "amount": amount or booking.total_price,
                        "payment_method": payment_method,
                        "notes": "Demo seed transaction.",
                        "created_by": staff_profile,
                    },
                )
                Transaction.objects.filter(pk=transaction_obj.pk).update(
                    created=created_at + timedelta(minutes=index),
                )
                transaction_obj.refresh_from_db()
                transactions[(club_key, booking_key)] = transaction_obj
        return transactions

    def create_settlements(self, profiles, clubs, courts, transactions):
        settlements = {}
        for club_key, club in clubs.items():
            owner_profile = profiles["owner"]
            admin_profile = profiles["platform_admin"]

            pending_transaction = transactions[(club_key, "pending")]
            pending_settlement, _ = Settlement.objects.update_or_create(
                club=club,
                notes="Demo pending settlement.",
                defaults={
                    "court": courts[club_key][1],
                    "total_amount": pending_transaction.amount,
                    "settled_by": owner_profile,
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
                notes="Demo settled settlement.",
                defaults={
                    "court": courts[club_key][1],
                    "total_amount": settled_transaction.amount,
                    "settled_by": admin_profile,
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
        profiles,
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
                actor = profiles[
                    "platform_admin" if actor_type == "platform" else actor_type
                ]
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
            before_data = {}
            after_data = {
                "settlement_id": settlement.id,
                "total_amount": str(settlement.total_amount),
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
        profiles,
        bookings,
        transactions,
        settlements,
        audit_logs,
    ):
        self.stdout.write(self.style.SUCCESS("Seeded demo data successfully."))
        self.stdout.write("")
        self.stdout.write("Users:")
        for user in users.values():
            self.stdout.write(f"- {user.phone_number} / {DEMO_PASSWORD}")
        self.stdout.write("")
        self.stdout.write("Clubs:")
        for club in clubs.values():
            self.stdout.write(f"- {club.slug}")
        self.stdout.write("")
        self.stdout.write("Useful checks:")
        self.stdout.write("- owner can settle in testing")
        self.stdout.write("- staff can access only Testing Main Court")
        self.stdout.write("- staff cannot access Testing Secondary Court")
        self.stdout.write("")
        self.stdout.write("Endpoints:")
        self.stdout.write("- /api/v1/auth/token/")
        self.stdout.write("- /api/v1/me/")
        self.stdout.write("- /api/v1/clubs/")
        self.stdout.write("- /api/v1/clubs/testing/bookings/")
        self.stdout.write("- /api/v1/clubs/testing/transactions/")
        self.stdout.write("- /api/v1/clubs/testing/settlements/preview/")
        self.stdout.write("- /api/v1/clubs/testing/audit-logs/")
        self.stdout.write("")
        self.stdout.write(
            "Counts: "
            f"users={len(users)}, clubs={len(clubs)}, "
            f"courts={sum(len(club_courts) for club_courts in courts.values())}, "
            f"profiles={len(profiles)}, bookings={len(bookings)}, "
            f"transactions={len(transactions)}, settlements={len(settlements)}, "
            f"audit_logs={len(audit_logs)}"
        )
