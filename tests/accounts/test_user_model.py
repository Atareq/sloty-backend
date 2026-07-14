from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.permissions import IsPlatformSuperAdmin


class UserModelTests(TestCase):
    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password="test-pass-123",
            **extra_fields,
        )

    def test_user_no_longer_requires_business_role(self):
        user = self.create_user(username="normal-user")

        self.assertFalse(hasattr(user, "role"))
        self.assertFalse(hasattr(user, "club"))
        self.assertFalse(hasattr(user, "court"))
        self.assertFalse(user.is_platform_admin)

    def test_platform_admin_flag_controls_platform_helper(self):
        user = self.create_user(username="platform-admin", is_platform_admin=True)

        self.assertTrue(user.is_platform_admin)
        self.assertTrue(user.is_platform_super_admin())

    def test_required_fields_do_not_include_role(self):
        self.assertEqual(User.REQUIRED_FIELDS, ["email"])

    def test_created_by_can_be_null(self):
        user = self.create_user(
            username="created-by-null",
            created_by=None,
        )

        self.assertIsNone(user.created_by)

    def test_created_by_can_point_to_another_user(self):
        creator = self.create_user(
            username="creator",
            is_platform_admin=True,
        )

        user = self.create_user(
            username="created-by-user",
            created_by=creator,
        )

        self.assertEqual(user.created_by, creator)

    def test_phone_number_allows_null_and_blank(self):
        field = User._meta.get_field("phone_number")

        self.assertTrue(field.null)
        self.assertTrue(field.blank)

        null_phone_user = self.create_user(
            username="null-phone",
            phone_number=None,
        )
        blank_phone_user = self.create_user(
            username="blank-phone",
            phone_number="",
        )

        self.assertIsNone(null_phone_user.phone_number)
        self.assertEqual(blank_phone_user.phone_number, "")


class PlatformPermissionTests(TestCase):
    def make_request(self, user):
        return SimpleNamespace(user=user)

    def test_permission_allows_platform_admin(self):
        user = User.objects.create_user(
            username="allowed-platform-admin",
            password="test-pass-123",
            is_platform_admin=True,
        )

        self.assertTrue(
            IsPlatformSuperAdmin().has_permission(self.make_request(user), view=None)
        )

    def test_permission_rejects_normal_user(self):
        user = User.objects.create_user(
            username="normal-user",
            password="test-pass-123",
        )

        self.assertFalse(
            IsPlatformSuperAdmin().has_permission(self.make_request(user), view=None)
        )

    def test_permission_rejects_anonymous_user(self):
        self.assertFalse(
            IsPlatformSuperAdmin().has_permission(
                self.make_request(AnonymousUser()),
                view=None,
            )
        )
