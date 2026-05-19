from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
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
            "city": "Assiut",
            "area": "Downtown",
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
    def test_active_user_can_obtain_token_without_club_slug(self):
        user = self.create_user(username="active-token-user")

        response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "username": user.username,
                "password": self.password,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_invalid_credentials_fail(self):
        user = self.create_user(username="invalid-token-user")

        response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "username": user.username,
                "password": "wrong-password",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_cannot_obtain_token(self):
        user = self.create_user(username="inactive-token-user", is_active=False)

        response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "username": user.username,
                "password": self.password,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


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

    def test_platform_admin_can_create_user(self):
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

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="created-user")
        self.assertTrue(user.check_password("new-user-pass-123"))
        self.assertEqual(user.created_by, self.platform_admin)
        self.assertFalse(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertEqual(response.data["created_by"], self.platform_admin.id)
        self.assertNotIn("password", response.data)

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
        self.assertTrue(response.data["is_platform_admin"])

    def test_create_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "flagged-user",
                "password": "new-user-pass-123",
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="flagged-user")
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
