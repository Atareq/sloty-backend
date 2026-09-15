"""
Integration tests for User Account Lifecycle, Membership Lifecycle, Role Change Safety,
Delegation Interactions, and Identity Independence.
"""

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court
from apps.players.models import ClubPlayer, PlayerProfile
from apps.settlements.services import create_approved_settlement
from apps.transactions.models import Transaction
from tests.booking_factories import persist_booking


class AccountAndMembershipLifecycleTests(APITestCase):
    password = "Test-pass-1234!"

    def setUp(self):
        self.club_a = Club.objects.create(
            name="Alpha Club",
            slug="alpha-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.club_b = Club.objects.create(
            name="Beta Club",
            slug="beta-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court_a1 = Court.objects.create(
            club=self.club_a,
            name="Court A1",
            default_price=Decimal("200.00"),
            slot_duration_minutes=60,
        )
        self.court_a2 = Court.objects.create(
            club=self.club_a,
            name="Court A2",
            default_price=Decimal("250.00"),
            slot_duration_minutes=60,
        )
        self.court_b1 = Court.objects.create(
            club=self.club_b,
            name="Court B1",
            default_price=Decimal("300.00"),
            slot_duration_minutes=60,
        )

        self.owner_a = self.create_user("owner-a")
        self.membership_owner_a = ClubMembership.objects.create(
            club=self.club_a,
            user=self.owner_a,
            role=ClubMembership.Role.OWNER,
            is_active=True,
        )

    def create_user(self, username: str, **extra) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra,
        )

    def obtain_token(self, username: str, club_slug: str = "") -> dict:
        payload = {"username": username, "password": self.password}
        if club_slug:
            payload["club_slug"] = club_slug
        response = self.client.post(
            reverse("token_obtain_pair"), payload, format="json"
        )
        return response.data

    # =========================================================================
    # 1. USER ACCOUNT LIFECYCLE
    # =========================================================================

    def test_active_user_authentication_flow(self):
        user = self.create_user("active-lifecycle-user")
        tokens = self.obtain_token(user.username)
        self.assertIn("access", tokens)
        self.assertIn("refresh", tokens)

        # Access /me
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        me_res = self.client.get(reverse("me"))
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)
        self.assertEqual(me_res.data["username"], user.username)

        # Refresh token
        ref_res = self.client.post(
            reverse("token_refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(ref_res.status_code, status.HTTP_200_OK)
        self.assertIn("access", ref_res.data)

    def test_deactivated_user_cannot_login_or_refresh_or_access_api(self):
        user = self.create_user("deactivated-lifecycle-user")
        tokens = self.obtain_token(user.username)

        # Deactivate user account
        user.is_active = False
        user.save(update_fields=["is_active"])

        # 1. Login fails
        login_res = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(login_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Refresh fails
        ref_res = self.client.post(
            reverse("token_refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(ref_res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(ref_res.data["code"], "USER_INACTIVE")

        # 3. Existing access token fails
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        me_res = self.client.get(reverse("me"))
        self.assertEqual(me_res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(me_res.data["code"], "USER_INACTIVE")

    def test_reactivated_user_can_authenticate_again(self):
        user = self.create_user("reactivated-lifecycle-user")
        user.is_active = False
        user.save(update_fields=["is_active"])

        # Inactive login fails
        res1 = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_401_UNAUTHORIZED)

        # Reactivate
        user.is_active = True
        user.save(update_fields=["is_active"])

        # Login succeeds
        res2 = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": self.password},
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)

    def test_password_change_invalidates_existing_tokens_immediately(self):
        user = self.create_user("pw-change-user")
        tokens = self.obtain_token(user.username)

        # Change password via API
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        new_password = "Brand-new-password-456!"
        change_res = self.client.post(
            reverse("password-change"),
            {
                "current_password": self.password,
                "new_password": new_password,
                "new_password_confirmation": new_password,
            },
            format="json",
        )
        self.assertEqual(change_res.status_code, status.HTTP_204_NO_CONTENT)

        # Old access token is rejected on subsequent request
        old_access_res = self.client.get(reverse("me"))
        self.assertEqual(old_access_res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(old_access_res.data["code"], "PASSWORD_CHANGED")

        # Old refresh token is rejected
        old_refresh_res = self.client.post(
            reverse("token_refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(old_refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(old_refresh_res.data["code"], "PASSWORD_CHANGED")

        # New credentials work
        new_token_res = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": new_password},
            format="json",
        )
        self.assertEqual(new_token_res.status_code, status.HTTP_200_OK)

    # =========================================================================
    # 2. MEMBERSHIP LIFECYCLE VS TOKEN CLAIMS
    # =========================================================================

    def test_deactivated_membership_revokes_club_access_immediately(self):
        staff = self.create_user("lifecycle-staff")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )
        # Token minted with club claims while active
        tokens = self.obtain_token(staff.username, club_slug=self.club_a.slug)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        # Verify active access works
        court_url = reverse(
            "club-court-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": self.court_a1.id},
        )
        res_active = self.client.get(court_url)
        self.assertEqual(res_active.status_code, status.HTTP_200_OK)

        # Deactivate membership in DB
        membership.is_active = False
        membership.save(update_fields=["is_active"])

        # User is still authenticated: /me succeeds
        me_res = self.client.get(reverse("me"))
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)

        # But club access is immediately denied: CLUB_ACCESS_REVOKED (403)
        res_revoked = self.client.get(court_url)
        self.assertEqual(res_revoked.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res_revoked.data["code"], "CLUB_ACCESS_REVOKED")

    def test_reactivated_membership_restores_access_with_existing_token(self):
        staff = self.create_user("reactivated-staff")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=False,
        )
        tokens = self.obtain_token(staff.username)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        court_url = reverse(
            "club-court-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": self.court_a1.id},
        )
        # Initially deactivated -> 403
        self.assertEqual(
            self.client.get(court_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Owner reactivates membership
        membership.is_active = True
        membership.save(update_fields=["is_active"])

        # Exact same access token now succeeds immediately
        res = self.client.get(court_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_soft_deleted_membership_cannot_be_reactivated_or_recreated(self):
        staff = self.create_user("deleted-staff")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )
        # Soft delete membership
        membership.deleted_at = timezone.now()
        membership.deleted_by = self.owner_a
        membership.save(update_fields=["deleted_at", "deleted_by"])

        # Authenticate as Owner
        owner_tokens = self.obtain_token(
            self.owner_a.username, club_slug=self.club_a.slug
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {owner_tokens['access']}")

        # Attempt to reactivate via PATCH -> 409 Conflict
        detail_url = reverse(
            "club-membership-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": membership.id},
        )
        patch_res = self.client.patch(detail_url, {"is_active": True}, format="json")
        # Since filter_scoped_queryset filters deleted_at__isnull=True,
        # detail lookup returns 404
        self.assertEqual(patch_res.status_code, status.HTTP_404_NOT_FOUND)

        # Attempt to recreate exact same (club, user, role, court) tuple -> 409 Conflict
        create_url = reverse(
            "club-membership-list", kwargs={"club_slug": self.club_a.slug}
        )
        post_res = self.client.post(
            create_url,
            {
                "user_id": staff.id,
                "role": "STAFF",
                "court": self.court_a1.id,
            },
            format="json",
        )
        self.assertEqual(post_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("MEMBERSHIP_DELETED_CANNOT_RECREATE", str(post_res.data))

    # =========================================================================
    # 3. ROLE CHANGE SAFETY
    # =========================================================================

    def test_role_change_from_manager_to_staff_takes_immediate_effect(self):
        user = self.create_user("role-change-user")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=user,
            role=ClubMembership.Role.MANAGER,
            is_active=True,
        )
        tokens = self.obtain_token(user.username, club_slug=self.club_a.slug)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        # As Manager: can list club users
        users_url = reverse("club-user-list", kwargs={"club_slug": self.club_a.slug})
        res1 = self.client.get(users_url)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        # As Manager: can see court_a2 (not restricted to assigned court)
        court2_url = reverse(
            "club-court-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": self.court_a2.id},
        )
        self.assertEqual(self.client.get(court2_url).status_code, status.HTTP_200_OK)

        # Demote role in DB to STAFF assigned to court_a1 only
        membership.role = ClubMembership.Role.STAFF
        membership.court = self.court_a1
        membership.save(update_fields=["role", "court"])

        # Stale token from Manager session is reused on next request:
        # 1. Listing club users is matrix-denied for STAFF (403)
        res_users_staff = self.client.get(users_url)
        self.assertEqual(res_users_staff.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Accessing non-assigned court_a2 is out-of-scope (404)
        res_court2_staff = self.client.get(court2_url)
        self.assertEqual(res_court2_staff.status_code, status.HTTP_404_NOT_FOUND)

        # 3. Accessing assigned court_a1 succeeds (200)
        court1_url = reverse(
            "club-court-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": self.court_a1.id},
        )
        res_court1_staff = self.client.get(court1_url)
        self.assertEqual(res_court1_staff.status_code, status.HTTP_200_OK)

    # =========================================================================
    # 4. DELEGATION INTERACTIONS
    # =========================================================================

    def test_manager_pricing_delegation_lifecycle(self):
        from apps.common.authorization.context import RequestAccessContext
        from apps.common.authorization.roles import Role
        from apps.courts.authorization import can_manage_working_hours

        manager = self.create_user("pricing-delegate")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=manager,
            role=ClubMembership.Role.MANAGER,
            manager_can_change_pricing=True,
            is_active=True,
        )

        ctx = RequestAccessContext(
            user=manager,
            role=Role.MANAGER,
            club=self.club_a,
            membership=membership,
        )
        # Delegation flag active -> permitted
        self.assertTrue(can_manage_working_hours(ctx, self.court_a1))

        # Flag revoked
        membership.manager_can_change_pricing = False
        membership.save(update_fields=["manager_can_change_pricing"])
        ctx_revoked = RequestAccessContext(
            user=manager,
            role=Role.MANAGER,
            club=self.club_a,
            membership=membership,
        )
        self.assertFalse(can_manage_working_hours(ctx_revoked, self.court_a1))

        # Demote to staff
        membership.role = ClubMembership.Role.STAFF
        membership.court = self.court_a1
        membership.manager_can_change_pricing = False
        membership.save(update_fields=["role", "court", "manager_can_change_pricing"])
        ctx_staff = RequestAccessContext(
            user=manager,
            role=Role.STAFF,
            club=self.club_a,
            membership=membership,
            court=self.court_a1,
        )
        self.assertFalse(can_manage_working_hours(ctx_staff, self.court_a1))

    def test_offboarding_custody_settlement_guard(self):
        staff = self.create_user("custody-offboard-staff")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )
        # Create booking and payment collected by this staff member
        start_time = timezone.now() + timezone.timedelta(days=1)
        booking = persist_booking(
            self.court_a1,
            start_time=start_time,
            end_time=start_time + timezone.timedelta(hours=1),
            total_price=Decimal("200.00"),
            created_by=staff,
            customer_name="Custody Guard Customer",
            customer_phone="+201099999001",
        )
        Transaction.objects.create(
            club=self.club_a,
            court=self.court_a1,
            booking=booking,
            created_by=staff,
            transaction_type=Transaction.Type.PAYMENT,
            amount=Decimal("200.00"),
            payment_method=Transaction.PaymentMethod.CASH,
            occurred_at=timezone.now(),
        )

        # Authenticate as Owner to attempt deactivation and deletion
        owner_tokens = self.obtain_token(
            self.owner_a.username, club_slug=self.club_a.slug
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {owner_tokens['access']}")
        detail_url = reverse(
            "club-membership-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": membership.id},
        )

        # 1. Attempt deactivation -> 409 Conflict
        patch_res = self.client.patch(detail_url, {"is_active": False}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            patch_res.data["code"],
            "MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED",
        )

        # 2. Attempt soft deletion -> 409 Conflict
        del_res = self.client.delete(detail_url)
        self.assertEqual(del_res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            del_res.data["code"],
            "MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED",
        )

        # Settle the custody
        create_approved_settlement(
            club=self.club_a,
            collected_by=staff,
            actor=self.owner_a,
        )

        # 3. Now deactivation succeeds
        patch_ok = self.client.patch(detail_url, {"is_active": False}, format="json")
        self.assertEqual(patch_ok.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertFalse(membership.is_active)

    # =========================================================================
    # 5. CROSS-CLUB ISOLATION
    # =========================================================================

    def test_cross_club_isolation_fails_closed(self):
        staff = self.create_user("club-a-staff")
        ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )
        tokens = self.obtain_token(staff.username)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        # Accessing Club B court list -> 403 CLUB_ACCESS_REVOKED
        b_url = reverse("club-court-list", kwargs={"club_slug": self.club_b.slug})
        res = self.client.get(b_url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data["code"], "CLUB_ACCESS_REVOKED")

        # Direct ID attack: accessing Court B1 using Club A URL -> 404
        attack_url = reverse(
            "club-court-detail",
            kwargs={"club_slug": self.club_a.slug, "pk": self.court_b1.id},
        )
        res_attack = self.client.get(attack_url)
        self.assertEqual(res_attack.status_code, status.HTTP_404_NOT_FOUND)

    # =========================================================================
    # 6. IDENTITY INDEPENDENCE
    # =========================================================================

    def test_user_creation_and_auth_never_create_player_identities(self):
        initial_profiles = PlayerProfile.objects.count()
        initial_club_players = ClubPlayer.objects.count()

        # Create user
        user = self.create_user("independent-user-test")
        # Obtain tokens
        self.obtain_token(user.username)

        self.assertEqual(PlayerProfile.objects.count(), initial_profiles)
        self.assertEqual(ClubPlayer.objects.count(), initial_club_players)

    def test_user_offboarding_does_not_affect_player_identities_or_bookings(self):
        staff = self.create_user("offboard-integrity-staff")
        membership = ClubMembership.objects.create(
            club=self.club_a,
            user=staff,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )
        start_time = timezone.now() + timezone.timedelta(days=2)
        booking = persist_booking(
            self.court_a1,
            start_time=start_time,
            end_time=start_time + timezone.timedelta(hours=1),
            total_price=Decimal("200.00"),
            created_by=staff,
            customer_name="Persistent Customer",
            customer_phone="+201011112222",
        )
        club_player_id = booking.club_player_id
        profile_id = booking.club_player.player_profile_id

        # Deactivate staff user and membership
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        staff.is_active = False
        staff.save(update_fields=["is_active"])

        # Customer and booking identities remain 100% intact
        booking.refresh_from_db()
        self.assertEqual(booking.club_player_id, club_player_id)
        self.assertTrue(ClubPlayer.objects.filter(id=club_player_id).exists())
        self.assertTrue(PlayerProfile.objects.filter(id=profile_id).exists())
