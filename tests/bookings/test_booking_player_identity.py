"""
Booking Identity Finalization — Phase A + Sprint 2 (Identity Enforcement).

Covers the Booking -> ClubPlayer -> PlayerProfile identity resolution flow
owned by apps.bookings.services.create_booking(), plus Sprint 2 enforcement:
never reusing a soft-deleted ClubPlayer, and propagating club_player to
recurring continuation bookings created by complete_booking(). See
apps/bookings/AGENTS.md ("Customer Identity Architecture (Locked)") and
docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md.

Scope: identity linkage only. No authorization/permission behavior is
exercised beyond what is required to reach the service layer through the
real API for the compatibility and recurrence tests.
"""

from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import create_booking, validate_booking_club_player
from apps.clubs.models import Club
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court, CourtWorkingHour, CourtWorkingHourPricePeriod
from apps.players.models import ClubPlayer, PlayerProfile
from apps.players.services import supersede_club_player
from apps.transactions.models import Transaction


class BookingPlayerIdentityTestCase(APITestCase):
    password = "test-pass-123"

    def create_platform_admin(self, username="identity-admin") -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            is_platform_admin=True,
        )

    def create_club(self, name: str, slug: str) -> Club:
        return Club.objects.create(
            name=name,
            slug=slug,
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )

    def create_court(self, club: Club, name: str) -> Court:
        court = Court.objects.create(
            club=club,
            name=name,
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
            cancellation_refund_notice_days=0,
        )
        working_hour = CourtWorkingHour.objects.create(court=court, weekday=2)
        CourtWorkingHourPricePeriod.objects.create(
            working_hour=working_hour,
            starts_at=time(9, 0),
            ends_at=time(23, 0),
            price=court.default_price,
        )
        return court

    def time_at(self, hour: int, minute: int = 0):
        # 2026-05-20 is a Wednesday (weekday=2) to match create_court()'s
        # working hours fixture above.
        return timezone.datetime(
            2026,
            5,
            20,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
        )


class SamePhoneSameClubTests(BookingPlayerIdentityTestCase):
    def test_same_phone_same_club_reuses_single_club_player(self):
        club = self.create_club("Identity Club A", "identity-club-a")
        court = self.create_court(club, "Court 1")
        admin = self.create_platform_admin()

        booking_one = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Ahmed Ali",
            customer_phone="+201012345678",
        )
        booking_two = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(12),
            end_time=self.time_at(13),
            customer_name="Ahmed Ali",
            customer_phone="+201012345678",
        )

        self.assertEqual(PlayerProfile.objects.count(), 1)
        self.assertEqual(ClubPlayer.objects.count(), 1)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertIsNotNone(booking_one.club_player_id)
        self.assertEqual(booking_one.club_player_id, booking_two.club_player_id)
        self.assertEqual(booking_one.club_player.club_id, club.id)
        self.assertEqual(
            str(booking_one.club_player.player_profile.phone_number),
            "+201012345678",
        )
        # Snapshot fields remain independently stored on each booking.
        self.assertEqual(booking_one.customer_name, "Ahmed Ali")
        self.assertEqual(booking_two.customer_name, "Ahmed Ali")


class SamePhoneDifferentClubsTests(BookingPlayerIdentityTestCase):
    def test_same_phone_different_clubs_creates_separate_club_players(self):
        club_a = self.create_club("Identity Club A2", "identity-club-a2")
        club_b = self.create_club("Identity Club B2", "identity-club-b2")
        court_a = self.create_court(club_a, "Court A")
        court_b = self.create_court(club_b, "Court B")
        admin = self.create_platform_admin()

        booking_a = create_booking(
            created_by=admin,
            court=court_a,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Mohamed Salah",
            customer_phone="+201099999999",
        )
        booking_b = create_booking(
            created_by=admin,
            court=court_b,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Mo Salah",
            customer_phone="+201099999999",
        )

        self.assertEqual(PlayerProfile.objects.count(), 1)
        self.assertEqual(ClubPlayer.objects.count(), 2)
        self.assertNotEqual(booking_a.club_player_id, booking_b.club_player_id)
        self.assertEqual(
            booking_a.club_player.player_profile_id,
            booking_b.club_player.player_profile_id,
        )
        self.assertEqual(booking_a.club_player.club_id, club_a.id)
        self.assertEqual(booking_b.club_player.club_id, club_b.id)
        # Each club's naming/label of the same player stays independent —
        # the global PlayerProfile.full_name is only set on first creation.
        self.assertEqual(booking_a.club_player.display_name, "Mohamed Salah")
        self.assertEqual(booking_b.club_player.display_name, "Mo Salah")


class ClubIsolationTests(BookingPlayerIdentityTestCase):
    def test_booking_model_clean_rejects_cross_club_club_player(self):
        club_a = self.create_club("Isolation Club A", "isolation-club-a")
        club_b = self.create_club("Isolation Club B", "isolation-club-b")
        court_a = self.create_court(club_a, "Court A")
        profile = PlayerProfile.objects.create(phone_number="+201055555555")
        other_club_player = ClubPlayer.objects.create(
            club=club_b,
            player_profile=profile,
        )

        booking = Booking(
            club=club_a,
            court=court_a,
            club_player=other_club_player,
            customer_name="Cross Club Attempt",
            customer_phone="+201055555555",
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            total_price=Decimal("300.00"),
        )

        with self.assertRaises(DjangoValidationError) as ctx:
            booking.full_clean()
        self.assertIn("club_player", ctx.exception.message_dict)

    def test_service_validation_rejects_cross_club_club_player(self):
        club_a = self.create_club("Isolation Club A2", "isolation-club-a2")
        club_b = self.create_club("Isolation Club B2", "isolation-club-b2")
        profile = PlayerProfile.objects.create(phone_number="+201066666666")
        other_club_player = ClubPlayer.objects.create(
            club=club_b,
            player_profile=profile,
        )

        with self.assertRaises(SlotyAPIException) as ctx:
            validate_booking_club_player(club=club_a, club_player=other_club_player)
        self.assertEqual(ctx.exception.api_code, "BOOKING_CLUB_PLAYER_MISMATCH")

    def test_create_booking_never_resolves_club_player_from_another_club(self):
        club_a = self.create_club("Isolation Club A3", "isolation-club-a3")
        club_b = self.create_club("Isolation Club B3", "isolation-club-b3")
        court_a = self.create_court(club_a, "Court A3")
        admin = self.create_platform_admin()

        # A ClubPlayer already exists in Club B for this phone number.
        profile = PlayerProfile.objects.create(phone_number="+201044444444")
        club_b_player = ClubPlayer.objects.create(club=club_b, player_profile=profile)

        booking = create_booking(
            created_by=admin,
            court=court_a,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Same Phone Other Club",
            customer_phone="+201044444444",
        )

        self.assertNotEqual(booking.club_player_id, club_b_player.id)
        self.assertEqual(booking.club_player.club_id, club_a.id)
        self.assertEqual(booking.club_player.player_profile_id, profile.id)


class BookingCreationCompatibilityTests(BookingPlayerIdentityTestCase):
    def test_booking_creation_api_does_not_require_or_expose_player_ids(self):
        club = self.create_club("Compat Club", "compat-club")
        court = self.create_court(club, "Compat Court")
        admin = self.create_platform_admin()
        self.client.force_authenticate(user=admin)

        payload = {
            "court": court.id,
            "customer_name": "Walk-in Customer",
            "customer_phone": "+201077777777",
            "start_time": self.time_at(10).isoformat(),
            "end_time": self.time_at(11).isoformat(),
        }
        response = self.client.post(
            reverse("club-booking-list", kwargs={"club_slug": club.slug}),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("club_player", response.data)
        self.assertNotIn("player_profile", response.data)

        booking = Booking.objects.get(id=response.data["id"])
        self.assertIsNotNone(booking.club_player_id)
        self.assertEqual(booking.club_player.club_id, club.id)
        self.assertEqual(
            str(booking.club_player.player_profile.phone_number),
            "+201077777777",
        )
        # Historical snapshot fields remain the response's identity contract.
        self.assertEqual(response.data["customer_name"], "Walk-in Customer")
        self.assertEqual(response.data["customer_phone"], "+201077777777")


class ClubPlayerSupersedeBookingHistoryTests(BookingPlayerIdentityTestCase):
    """
    ClubPlayer Sprint 1 — Booking.club_player is the historical snapshot.

    Superseding a ClubPlayer (soft-delete + new active row, see
    apps.players.services.supersede_club_player) must never affect any
    Booking that already references the old row — that FK IS the identity
    as of when the booking was made.
    """

    def test_existing_booking_keeps_referencing_superseded_club_player(self):
        club = self.create_club("Supersede Club", "supersede-club")
        court = self.create_court(club, "Court 1")
        admin = self.create_platform_admin()

        booking = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Ahmed Ali",
            customer_phone="+201088888888",
        )
        original_club_player_id = booking.club_player_id
        original_club_player = booking.club_player

        new_club_player = supersede_club_player(
            original_club_player, display_name="Ahmed Salah", player_number=11
        )

        booking.refresh_from_db()
        self.assertEqual(booking.club_player_id, original_club_player_id)
        self.assertNotEqual(booking.club_player_id, new_club_player.id)
        # The booking's historical view of the player is untouched.
        self.assertEqual(booking.club_player.display_name, "Ahmed Ali")
        self.assertTrue(
            ClubPlayer.objects.get(pk=original_club_player_id).deleted_at is not None
        )

    def test_new_booking_after_supersede_resolves_the_new_active_club_player(self):
        club = self.create_club("Supersede Club 2", "supersede-club-2")
        court = self.create_court(club, "Court 1")
        admin = self.create_platform_admin()

        old_booking = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Ahmed Ali",
            customer_phone="+201088889999",
        )
        new_club_player = supersede_club_player(
            old_booking.club_player, display_name="Ahmed Salah", player_number=11
        )

        new_booking = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(12),
            end_time=self.time_at(13),
            customer_name="Ahmed Salah",
            customer_phone="+201088889999",
        )

        self.assertEqual(new_booking.club_player_id, new_club_player.id)
        self.assertNotEqual(new_booking.club_player_id, old_booking.club_player_id)
        old_booking.refresh_from_db()
        self.assertEqual(old_booking.club_player.display_name, "Ahmed Ali")


class DeletedClubPlayerResolutionTests(BookingPlayerIdentityTestCase):
    """
    Sprint 2 — ClubPlayer resolution rules.

    A soft-deleted ClubPlayer (deleted_at set, with no active replacement
    yet created) must never be reused by create_booking(). The service must
    resolve/create a fresh active ClubPlayer for the same (club, profile)
    pair instead.
    """

    def test_booking_creation_never_reuses_a_soft_deleted_club_player(self):
        club = self.create_club("Deleted Identity Club", "deleted-identity-club")
        court = self.create_court(club, "Court 1")
        admin = self.create_platform_admin()

        profile = PlayerProfile.objects.create(
            phone_number="+201091112222", full_name="Ahmed Ali"
        )
        deleted_club_player = ClubPlayer.objects.create(
            club=club, player_profile=profile, display_name="Ahmed Ali"
        )
        deleted_club_player.deleted_at = timezone.now()
        deleted_club_player.save(update_fields=["deleted_at", "updated_at"])

        booking = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Ahmed Ali",
            customer_phone="+201091112222",
        )

        self.assertIsNotNone(booking.club_player_id)
        self.assertNotEqual(booking.club_player_id, deleted_club_player.id)
        self.assertTrue(booking.club_player.is_active)
        self.assertEqual(booking.club_player.player_profile_id, profile.id)
        # The deleted row remains untouched — never resurrected.
        deleted_club_player.refresh_from_db()
        self.assertIsNotNone(deleted_club_player.deleted_at)
        self.assertEqual(
            ClubPlayer.objects.filter(
                club=club, player_profile=profile, deleted_at__isnull=True
            ).count(),
            1,
        )


class RecurringBookingIdentityPropagationTests(BookingPlayerIdentityTestCase):
    """
    Sprint 2 — Step 3: recurring continuation must preserve club_player.

    complete_booking(continue_recurring=True) creates the next occurrence
    directly (bypassing create_booking()/resolve_booking_club_player()), so
    it must explicitly copy the anchor booking's club_player rather than
    leaving it NULL.
    """

    def test_recurring_continuation_preserves_the_anchor_club_player(self):
        club = self.create_club("Recurring Identity Club", "recurring-identity-club")
        court = self.create_court(club, "Court 1")
        admin = self.create_platform_admin()
        self.client.force_authenticate(user=admin)

        anchor = create_booking(
            created_by=admin,
            court=court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            customer_name="Ahmed Ali",
            customer_phone="+201093334444",
            source=Booking.Source.RECURRING,
        )
        self.assertIsNotNone(anchor.club_player_id)

        # Bring the anchor to CONFIRMED via full payment, matching the real
        # lifecycle precondition for complete_booking().
        anchor.status = Booking.Status.CONFIRMED
        anchor.save(update_fields=["status"])
        Transaction.objects.create(
            club=club,
            court=court,
            booking=anchor,
            transaction_type=Transaction.Type.PAYMENT,
            amount=anchor.total_price,
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=admin,
        )

        response = self.client.post(
            reverse(
                "club-booking-complete",
                kwargs={"club_slug": club.slug, "pk": anchor.pk},
            ),
            {"continue_recurring": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        anchor.refresh_from_db()
        next_booking = anchor.next_recurring_booking

        self.assertIsNotNone(next_booking)
        self.assertIsNotNone(next_booking.club_player_id)
        self.assertEqual(next_booking.club_player_id, anchor.club_player_id)
        self.assertEqual(next_booking.club_player.club_id, club.id)
