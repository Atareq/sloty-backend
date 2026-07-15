from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court


class AccountAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="platform-admin") -> User:
        return self.create_user(username=username, is_platform_admin=True)

    def create_club(self, name: str, slug: str, **extra_fields) -> Club:
        data = {
            "name": name,
            "slug": slug,
            "governorate": "ASSIUT",
            "city": "ASSIUT_MARKAZ",
        }
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": "250.00",
        }
        data.update(extra_fields)
        return Court.objects.create(**data)

    def create_membership(self, user, club, role, court=None):
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
        )


class MeAPITests(AccountAPITestCase):
    def test_anonymous_user_gets_401(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_profile_with_active_memberships(self):
        user = self.create_user(
            username="profile-user",
            email="profile@example.com",
            first_name="Profile",
            last_name="User",
            phone_number="+201000000001",
        )
        owner_club = self.create_club("El Nasr", "el-nasr")
        staff_club = self.create_club("Champions", "champions")
        staff_court = self.create_court(staff_club, "Court 1")
        owner_membership = self.create_membership(
            user,
            owner_club,
            ClubMembership.Role.OWNER,
        )
        staff_membership = self.create_membership(
            user,
            staff_club,
            ClubMembership.Role.STAFF,
            court=staff_court,
        )
        inactive_club = self.create_club("Inactive Scope", "inactive-scope")
        ClubMembership.objects.create(
            club=inactive_club,
            user=user,
            role=ClubMembership.Role.OWNER,
            is_active=False,
        )
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "id",
                "username",
                "email",
                "first_name",
                "last_name",
                "phone_number",
                "is_active",
                "is_platform_admin",
                "memberships",
            },
        )
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["username"], user.username)
        self.assertFalse(response.data["is_platform_admin"])
        self.assertNotIn("password", response.data)

        memberships = {item["id"]: item for item in response.data["memberships"]}
        self.assertEqual(set(memberships), {owner_membership.id, staff_membership.id})
        self.assertEqual(memberships[owner_membership.id]["role"], "OWNER")
        self.assertEqual(memberships[owner_membership.id]["club"]["slug"], "el-nasr")
        self.assertIsNone(memberships[owner_membership.id]["court"])
        self.assertEqual(memberships[staff_membership.id]["role"], "STAFF")
        self.assertEqual(
            memberships[staff_membership.id]["club"]["slug"],
            "champions",
        )
        self.assertEqual(
            memberships[staff_membership.id]["court"],
            {"id": staff_court.id, "name": staff_court.name},
        )


class JWTAPITests(AccountAPITestCase):
    def obtain_token(self, username, **extra_data):
        payload = {
            "username": username,
            "password": self.password,
        }
        payload.update(extra_data)
        return self.client.post(
            reverse("token_obtain_pair"),
            payload,
            format="json",
        )

    def decode_access(self, token):
        return AccessToken(token)

    def test_active_user_can_obtain_token_without_club_slug(self):
        user = self.create_user(username="active-token-user")

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_invalid_credentials_fail(self):
        user = self.create_user(username="invalid-token-user")

        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_cannot_obtain_token(self):
        user = self.create_user(username="inactive-token-user", is_active=False)

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_global_normal_token_contains_base_custom_claims(self):
        user = self.create_user(
            username="claims-user",
            first_name="Claims",
            last_name="User",
        )

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["user_id"], user.id)
        self.assertEqual(claims["role"], "")
        self.assertEqual(claims["name"], "Claims User")
        self.assertNotIn("club_id", claims)
        self.assertNotIn("court_id", claims)

    def test_platform_admin_token_has_platform_role(self):
        admin = self.create_platform_admin(username="claims-admin")

        response = self.obtain_token(admin.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "PLATFORM_ADMIN")
        self.assertEqual(claims["name"], admin.username)

    def test_platform_admin_token_with_club_slug_includes_club_context(self):
        admin = self.create_platform_admin(username="claims-admin-club")
        club = self.create_club("Admin Claims Club", "admin-claims-club")

        response = self.obtain_token(admin.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "PLATFORM_ADMIN")
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_owner_token_with_club_slug_has_owner_claims(self):
        owner = self.create_user(username="claims-owner")
        club = self.create_club("Owner Claims Club", "owner-claims-club")
        self.create_membership(owner, club, ClubMembership.Role.OWNER)

        response = self.obtain_token(owner.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.OWNER)
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_manager_token_with_club_slug_has_manager_claims(self):
        manager = self.create_user(username="claims-manager")
        club = self.create_club("Manager Claims Club", "manager-claims-club")
        self.create_membership(manager, club, ClubMembership.Role.MANAGER)

        response = self.obtain_token(manager.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.MANAGER)
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_staff_token_with_club_slug_has_staff_and_court_claims(self):
        staff = self.create_user(username="claims-staff")
        club = self.create_club("Staff Claims Club", "staff-claims-club")
        court = self.create_court(club, "Staff Claims Court")
        self.create_membership(staff, club, ClubMembership.Role.STAFF, court=court)

        response = self.obtain_token(staff.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_invalid_club_slug_is_rejected(self):
        user = self.create_user(username="invalid-club-token-user")

        response = self.obtain_token(user.username, club_slug="missing-club")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("club_slug", response.data)

    def test_club_slug_without_membership_is_rejected_for_non_platform_user(self):
        user = self.create_user(username="unauthorized-club-token-user")
        club = self.create_club("Unauthorized Claims Club", "unauthorized-claims")

        response = self.obtain_token(user.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("club_slug", response.data)

    def test_refresh_preserves_custom_claims(self):
        staff = self.create_user(username="refresh-claims-staff")
        club = self.create_club("Refresh Claims Club", "refresh-claims")
        court = self.create_court(club, "Refresh Claims Court")
        self.create_membership(staff, club, ClubMembership.Role.STAFF, court=court)
        token_response = self.obtain_token(staff.username, club_slug=club.slug)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(refresh_response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_user_model_does_not_store_club_scoped_role_fields(self):
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)


class PlatformUserManagementAPITests(AccountAPITestCase):
    user_list_response_fields = {
        "id",
        "username",
        "email",
        "first_name",
        "last_name",
        "phone_number",
        "is_active",
        "is_platform_admin",
        "created_by",
    }

    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.non_platform_user = self.create_user(username="normal-user")

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_platform_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.non_platform_user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_admin_can_list_users(self):
        self.authenticate_platform_admin()

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            set(response.data["results"][0]), self.user_list_response_fields
        )
        self.assertNotIn("password", response.data["results"][0])

    def test_platform_admin_can_retrieve_user(self):
        self.authenticate_platform_admin()

        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.non_platform_user.id)
        self.assertEqual(response.data["username"], self.non_platform_user.username)
        self.assertNotIn("password", response.data)

    def test_platform_admin_cannot_create_non_platform_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "created-user",
                "password": "new-user-pass-123",
                "email": "created@example.com",
                "first_name": "Created",
                "last_name": "User",
                "phone_number": "+201000000002",
                "is_active": True,
                "is_platform_admin": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"][0],
            "Club users must be created through a club-scoped membership endpoint.",
        )
        self.assertFalse(User.objects.filter(username="created-user").exists())

    def test_platform_admin_can_create_platform_admin_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "created-admin",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="created-admin")
        self.assertTrue(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.created_by, self.platform_admin)
        self.assertTrue(response.data["is_platform_admin"])
        self.assertNotIn("password", response.data)

    def test_user_create_rejects_role_club_and_court_fields(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "bad-scope-user",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
                "role": ClubMembership.Role.STAFF,
                "club": 1,
                "court": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", response.data)
        self.assertIn("club", response.data)
        self.assertIn("court", response.data)
        self.assertFalse(User.objects.filter(username="bad-scope-user").exists())

    def test_create_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "flagged-user",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="flagged-user")
        self.assertTrue(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotIn("is_staff", response.data)
        self.assertNotIn("is_superuser", response.data)
        self.assertNotIn("password", response.data)

    def test_platform_admin_can_patch_user_identity_and_platform_flag(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {
                "email": "updated@example.com",
                "first_name": "Updated",
                "last_name": "User",
                "phone_number": "+201000000003",
                "is_platform_admin": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertEqual(self.non_platform_user.email, "updated@example.com")
        self.assertEqual(self.non_platform_user.first_name, "Updated")
        self.assertEqual(self.non_platform_user.last_name, "User")
        self.assertEqual(
            str(self.non_platform_user.phone_number),
            "+201000000003",
        )
        self.assertTrue(self.non_platform_user.is_platform_admin)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertTrue(response.data["is_platform_admin"])
        self.assertNotIn("password", response.data)

    def test_platform_admin_can_deactivate_user(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertFalse(self.non_platform_user.is_active)
        self.assertFalse(response.data["is_active"])

    def test_delete_user_is_not_allowed(self):
        self.authenticate_platform_admin()

        response = self.client.delete(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class OrphanBusinessUserIntegrityTests(AccountAPITestCase):
    def test_find_orphan_business_users_identifies_active_non_platform_orphans(self):
        orphan = self.create_user(username="orphan-business-user")
        inactive_user = self.create_user(username="inactive-business-user")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        platform_admin = self.create_platform_admin("orphan-platform-admin")
        club = self.create_club("Scoped Club", "scoped-club")
        scoped_user = self.create_user(username="scoped-business-user")
        self.create_membership(scoped_user, club, ClubMembership.Role.OWNER)

        orphan_ids = set(find_orphan_business_users().values_list("id", flat=True))

        self.assertIn(orphan.id, orphan_ids)
        self.assertNotIn(inactive_user.id, orphan_ids)
        self.assertNotIn(platform_admin.id, orphan_ids)
        self.assertNotIn(scoped_user.id, orphan_ids)
