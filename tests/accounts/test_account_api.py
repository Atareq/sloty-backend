from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User


class AccountAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, role: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            role=role,
            **extra_fields,
        )


class MeAPITests(AccountAPITestCase):
    def test_anonymous_user_gets_401(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_profile(self):
        user = self.create_user(
            username="profile-user",
            role=User.Role.MANAGER,
            email="profile@example.com",
            first_name="Profile",
            last_name="User",
            phone_number="+201000000001",
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
                "role",
                "phone_number",
                "is_active",
            },
        )
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["username"], user.username)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["first_name"], user.first_name)
        self.assertEqual(response.data["last_name"], user.last_name)
        self.assertEqual(response.data["role"], user.role)
        self.assertEqual(response.data["phone_number"], str(user.phone_number))
        self.assertTrue(response.data["is_active"])
        self.assertNotIn("password", response.data)


class JWTAPITests(AccountAPITestCase):
    def test_active_user_can_obtain_token(self):
        user = self.create_user(
            username="active-token-user",
            role=User.Role.STAFF,
        )

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
        user = self.create_user(
            username="invalid-token-user",
            role=User.Role.STAFF,
        )

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
        user = self.create_user(
            username="inactive-token-user",
            role=User.Role.STAFF,
            is_active=False,
        )

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
        "role",
        "phone_number",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_by",
    }

    def setUp(self):
        self.platform_admin = self.create_user(
            username="platform-admin",
            role=User.Role.PLATFORM_SUPER_ADMIN,
        )
        self.non_platform_user = self.create_user(
            username="club-owner",
            role=User.Role.CLUB_OWNER,
        )

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_platform_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.non_platform_user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_super_admin_can_list_users(self):
        self.authenticate_platform_admin()

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertNotIn("password", response.data["results"][0])

    def test_platform_super_admin_can_retrieve_user(self):
        self.authenticate_platform_admin()

        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.non_platform_user.id)
        self.assertEqual(response.data["username"], self.non_platform_user.username)
        self.assertNotIn("password", response.data)

    def test_platform_super_admin_can_create_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "created-staff",
                "password": "new-user-pass-123",
                "email": "created@example.com",
                "first_name": "Created",
                "last_name": "Staff",
                "role": User.Role.STAFF,
                "phone_number": "+201000000002",
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="created-staff")
        self.assertTrue(user.check_password("new-user-pass-123"))
        self.assertEqual(user.created_by, self.platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertEqual(response.data["created_by"], self.platform_admin.id)
        self.assertFalse(response.data["is_staff"])
        self.assertFalse(response.data["is_superuser"])
        self.assertNotIn("password", response.data)

    def test_create_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "flagged-user",
                "password": "new-user-pass-123",
                "role": User.Role.MANAGER,
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="flagged-user")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertFalse(response.data["is_staff"])
        self.assertFalse(response.data["is_superuser"])
        self.assertNotIn("password", response.data)

    def test_platform_super_admin_can_patch_safe_fields(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {
                "email": "updated@example.com",
                "first_name": "Updated",
                "last_name": "Owner",
                "role": User.Role.MANAGER,
                "phone_number": "+201000000003",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertEqual(self.non_platform_user.email, "updated@example.com")
        self.assertEqual(self.non_platform_user.first_name, "Updated")
        self.assertEqual(self.non_platform_user.last_name, "Owner")
        self.assertEqual(self.non_platform_user.role, User.Role.MANAGER)
        self.assertEqual(
            str(self.non_platform_user.phone_number),
            "+201000000003",
        )
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertEqual(response.data["username"], self.non_platform_user.username)
        self.assertEqual(response.data["email"], "updated@example.com")
        self.assertEqual(response.data["first_name"], "Updated")
        self.assertEqual(response.data["last_name"], "Owner")
        self.assertEqual(response.data["role"], User.Role.MANAGER)
        self.assertEqual(response.data["phone_number"], "+201000000003")
        self.assertFalse(response.data["is_staff"])
        self.assertFalse(response.data["is_superuser"])
        self.assertNotIn("password", response.data)

    def test_platform_super_admin_can_deactivate_user(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertFalse(self.non_platform_user.is_active)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertFalse(response.data["is_active"])

    def test_update_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertFalse(self.non_platform_user.is_staff)
        self.assertFalse(self.non_platform_user.is_superuser)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertFalse(response.data["is_staff"])
        self.assertFalse(response.data["is_superuser"])
        self.assertNotIn("password", response.data)

    def test_delete_user_is_not_allowed(self):
        self.authenticate_platform_admin()

        response = self.client.delete(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
