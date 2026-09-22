"""Tests for Club Staff Management API endpoints (/api/v1/clubs/{club_slug}/staff/)."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.models import Booking, BookingAttempt
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.players.models import ClubPlayer
from apps.profiles.models import Profile, StaffCourtAccess, StaffProfile
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction, TransactionAttempt
from tests.profile_factories import (
    create_admin_user,
    create_owner_user,
    create_player_profile,
    create_staff_user,
)


class ClubStaffAPITests(APITestCase):
    def setUp(self):
        self.club = Club.objects.create(
            name="Cairo Sports Club",
            slug="cairo-sports",
            governorate="CAIRO",
            city="NASR_CITY",
        )
        self.other_club = Club.objects.create(
            name="Alex Sports Club",
            slug="alex-sports",
            governorate="ALEXANDRIA",
            city="ALEXANDRIA",
        )
        self.court_1 = Court.objects.create(
            club=self.club, name="Court 1", default_price="200.00"
        )
        self.court_2 = Court.objects.create(
            club=self.club, name="Court 2", default_price="250.00"
        )
        self.other_court = Court.objects.create(
            club=self.other_club, name="Other Court", default_price="300.00"
        )

        self.owner = create_owner_user(clubs=[self.club])
        self.other_owner = create_owner_user(clubs=[self.other_club])
        self.admin = create_admin_user()
        self.staff_user = create_staff_user(club=self.club, courts=[self.court_1])

    def test_owner_can_list_staff_for_their_club(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["role"], "STAFF")
        self.assertEqual(item["phone_number"], str(self.staff_user.phone_number))
        self.assertEqual(len(item["courts"]), 1)
        self.assertEqual(item["courts"][0]["id"], self.court_1.id)

    def test_owner_can_create_staff_with_initial_courts(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "email": "tarek.owner@example.com",
            "password": "SecurePassword123!",
            "first_name": "Omar",
            "last_name": "Ali",
            "phone_number": "+201011112222",
            "court_ids": [self.court_1.id, self.court_2.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "STAFF")
        self.assertEqual(str(response.data["phone_number"]), "+201011112222")
        self.assertEqual(response.data["first_name"], "Omar")
        self.assertEqual(len(response.data["courts"]), 2)

        # Verify DB state
        created_user = User.objects.get(phone_number="+201011112222")
        self.assertTrue(created_user.check_password("SecurePassword123!"))
        profile = created_user.profile
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, Profile.Role.STAFF)
        self.assertEqual(profile.created_by_id, self.owner.profile.pk)
        self.assertEqual(profile.staff_profile.club, self.club)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 2)

    def test_create_staff_rejects_cross_club_court_assignment(self):
        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "email": "cross-club-staff@example.com",
            "password": "SecurePassword123!",
            "phone_number": "+201033334444",
            "court_ids": [self.court_1.id, self.other_court.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(phone_number="+201033334444").exists())
        self.assertFalse(
            StaffProfile.objects.filter(user__phone_number="+201033334444").exists()
        )
        self.assertFalse(
            StaffCourtAccess.objects.filter(
                staff_profile__user__phone_number="+201033334444"
            ).exists()
        )

    def test_admin_cannot_directly_create_staff(self):
        self.client.force_authenticate(user=self.admin)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "email": "admin-created-staff@example.com",
            "password": "SecurePassword123!",
            "phone_number": "+201099998888",
            "court_ids": [self.court_1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(phone_number="+201099998888").exists())

    def test_owner_cannot_create_staff_in_unowned_club(self):
        self.client.force_authenticate(user=self.other_owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        payload = {
            "email": "foreign-staff@example.com",
            "password": "SecurePassword123!",
            "phone_number": "+201099997777",
            "court_ids": [self.court_1.id],
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(phone_number="+201099997777").exists())

    def test_owner_can_retrieve_staff_detail(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["profile_id"], profile_id)
        self.assertEqual(
            str(response.data["phone_number"]), str(self.staff_user.phone_number)
        )

    def test_owner_can_patch_staff_details_and_toggle_is_enabled(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/"
        payload = {
            "first_name": "UpdatedName",
            "is_enabled": False,
        }
        response = self.client.patch(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first_name"], "UpdatedName")
        self.assertFalse(response.data["is_enabled"])

        profile.refresh_from_db()
        self.assertFalse(profile.is_enabled)
        self.staff_user.refresh_from_db()
        self.assertEqual(self.staff_user.first_name, "UpdatedName")

    def test_owner_can_replace_assigned_courts(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/courts/"

        # Assign both court 1 and court 2
        response = self.client.put(
            url,
            {"court_ids": [self.court_1.id, self.court_2.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["courts"]), 2)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 2)

        # Clear to 0 courts
        response = self.client.put(url, {"court_ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["courts"]), 0)
        self.assertEqual(profile.staff_profile.court_accesses.count(), 0)

    def test_replace_courts_rejects_foreign_club_court(self):
        self.client.force_authenticate(user=self.owner)
        profile = self.staff_user.profile
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile.id}/courts/"

        response = self.client.put(
            url,
            {"court_ids": [self.other_court.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_delete_staff_profile(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(Profile.objects.filter(id=profile_id).exists())
        self.assertTrue(StaffProfile.objects.filter(pk=profile_id).exists())
        staff_profile = StaffProfile.objects.get(pk=profile_id)
        self.assertFalse(staff_profile.is_enabled)
        self.staff_user.refresh_from_db()
        self.assertTrue(self.staff_user.is_active)
        self.assertEqual(staff_profile.court_accesses.count(), 0)

    def test_delete_staff_preserves_historical_booking(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        player_profile = create_player_profile("+201011112222", "Test Customer")
        club_player = ClubPlayer.objects.create(
            club=self.club,
            player_profile=player_profile,
            display_name="Test Customer",
        )
        start_time = timezone.now() + timedelta(days=1)
        booking = Booking.objects.create(
            club=self.club,
            court=self.court_1,
            club_player=club_player,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            total_price=Decimal("200.00"),
            created_by=self.staff_user.profile,
            last_status_changed_by=self.staff_user.profile,
            status=Booking.Status.CONFIRMED,
        )

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        booking.refresh_from_db()
        self.assertEqual(booking.created_by_id, profile_id)
        self.assertEqual(booking.last_status_changed_by_id, profile_id)
        self.assertIsNotNone(booking.created_by)
        self.assertEqual(
            booking.created_by.get_display_name(),
            self.staff_user.get_display_name(),
        )

    def test_delete_staff_preserves_historical_transaction(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        player_profile = create_player_profile("+201011113333", "Tx Customer")
        club_player = ClubPlayer.objects.create(
            club=self.club,
            player_profile=player_profile,
            display_name="Tx Customer",
        )
        start_time = timezone.now() + timedelta(days=1)
        booking = Booking.objects.create(
            club=self.club,
            court=self.court_1,
            club_player=club_player,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            total_price=Decimal("200.00"),
            created_by=self.staff_user.profile,
            status=Booking.Status.CONFIRMED,
        )
        transaction = Transaction.objects.create(
            club=self.club,
            court=self.court_1,
            booking=booking,
            amount=Decimal("100.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            created_by=self.staff_user.profile,
        )

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        transaction.refresh_from_db()
        self.assertEqual(transaction.created_by_id, profile_id)
        self.assertIsNotNone(transaction.created_by)
        self.assertEqual(
            transaction.created_by.get_display_name(),
            self.staff_user.get_display_name(),
        )

    def test_delete_staff_preserves_historical_settlement(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        settlement = Settlement.objects.create(
            club=self.club,
            court=self.court_1,
            total_amount=Decimal("500.00"),
            settled_by=self.staff_user.profile,
        )

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        settlement.refresh_from_db()
        self.assertEqual(settlement.settled_by_id, profile_id)
        self.assertIsNotNone(settlement.settled_by)
        self.assertEqual(
            settlement.settled_by.get_display_name(),
            self.staff_user.get_display_name(),
        )

    def test_delete_staff_preserves_historical_audit(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        audit_log = AuditLog.objects.create(
            club=self.club,
            court=self.court_1,
            actor=self.staff_user.profile,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=999,
        )

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        audit_log.refresh_from_db()
        self.assertEqual(audit_log.actor_id, profile_id)
        self.assertIsNotNone(audit_log.actor)

    def test_delete_staff_preserves_historical_attempts(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        start_time = timezone.now() + timedelta(days=1)
        booking_attempt = BookingAttempt.objects.create(
            club=self.club,
            court=self.court_1,
            attempted_by=self.staff_user.profile,
            customer_name="Attempt Customer",
            customer_phone="+201011114444",
            requested_start=start_time,
            requested_end=start_time + timedelta(hours=1),
            requested_at=timezone.now(),
            outcome="REJECTED",
            failure_code="OVERLAP",
        )

        player_profile = create_player_profile("+201011115555", "Tx Customer")
        club_player = ClubPlayer.objects.create(
            club=self.club,
            player_profile=player_profile,
            display_name="Tx Customer",
        )
        booking = Booking.objects.create(
            club=self.club,
            court=self.court_1,
            club_player=club_player,
            start_time=start_time,
            end_time=start_time + timedelta(hours=1),
            total_price=Decimal("200.00"),
            created_by=self.staff_user.profile,
            status=Booking.Status.CONFIRMED,
        )
        tx_attempt = TransactionAttempt.objects.create(
            club=self.club,
            court=self.court_1,
            booking=booking,
            attempted_by=self.staff_user.profile,
            amount=Decimal("100.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            occurred_at=timezone.now(),
            outcome="REJECTED",
            failure_code="INSUFFICIENT_FUNDS",
        )

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        booking_attempt.refresh_from_db()
        tx_attempt.refresh_from_db()
        self.assertEqual(booking_attempt.attempted_by_id, profile_id)
        self.assertEqual(tx_attempt.attempted_by_id, profile_id)

    def test_delete_staff_immediately_revokes_operational_access(self):
        # Staff can initially access assigned court
        self.client.force_authenticate(user=self.staff_user)
        courts_url = f"/api/v1/clubs/{self.club.slug}/courts/"
        resp_before = self.client.get(courts_url)
        self.assertEqual(resp_before.status_code, status.HTTP_200_OK)

        # Owner deletes staff
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        delete_url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        delete_resp = self.client.delete(delete_url)
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)

        # Staff authenticated requests now immediately fail with 403 CLUB_ACCESS_REVOKED
        self.client.force_authenticate(user=self.staff_user)
        resp_after = self.client.get(courts_url)
        self.assertEqual(resp_after.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp_after.data["code"], "CLUB_ACCESS_REVOKED")

    def test_delete_staff_preserves_user_account(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id
        user_id = self.staff_user.id
        phone_number = self.staff_user.phone_number
        email = self.staff_user.email

        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        user = User.objects.get(pk=user_id)
        self.assertTrue(user.is_active)
        self.assertEqual(user.phone_number, phone_number)
        self.assertEqual(user.email, email)

    def test_staff_reactivation_restores_access_with_same_profile_id(self):
        self.client.force_authenticate(user=self.owner)
        profile_id = self.staff_user.profile.id

        # Delete staff
        url = f"/api/v1/clubs/{self.club.slug}/staff/{profile_id}/"
        delete_resp = self.client.delete(url)
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)

        staff_profile = StaffProfile.objects.get(pk=profile_id)
        self.assertFalse(staff_profile.is_enabled)
        self.assertEqual(staff_profile.court_accesses.count(), 0)

        # Reactivate via PATCH with court assignment
        reactivate_resp = self.client.patch(
            url,
            {"is_enabled": True, "court_ids": [self.court_1.id]},
            format="json",
        )
        self.assertEqual(reactivate_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(reactivate_resp.data["is_enabled"])
        self.assertEqual(reactivate_resp.data["profile_id"], profile_id)

        staff_profile.refresh_from_db()
        self.assertTrue(staff_profile.is_enabled)
        self.assertEqual(staff_profile.court_accesses.count(), 1)
        self.assertEqual(staff_profile.pk, profile_id)

        # Operational access is restored
        self.client.force_authenticate(user=self.staff_user)
        courts_url = f"/api/v1/clubs/{self.club.slug}/courts/"
        resp = self.client.get(courts_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_staff_role_cannot_access_staff_management_api(self):
        self.client.force_authenticate(user=self.staff_user)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_club_owner_denied_access(self):
        self.client.force_authenticate(user=self.other_owner)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_admin_can_manage_staff_across_clubs(self):
        self.client.force_authenticate(user=self.admin)
        url = f"/api/v1/clubs/{self.club.slug}/staff/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
