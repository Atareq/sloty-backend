from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.permissions import IsPlatformSuperAdmin
from apps.profiles.models import AdminProfile, Profile


class UserModelTests(TestCase):
    def test_authentication_account_can_exist_before_profile_provisioning(self):
        user = User.objects.create_user(
            username="pending-profile", password="test-pass-123"
        )
        self.assertTrue(user.check_password("test-pass-123"))
        self.assertNotIn("role", {field.name for field in User._meta.local_fields})
        self.assertNotIn("created_by", {field.name for field in User._meta.fields})
        self.assertFalse(hasattr(user, "role"))
        self.assertFalse(hasattr(user, "is_platform_super_admin"))

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password="test-pass-123",
            **extra_fields,
        )

    def test_admin_profile_controls_platform_permission(self):
        user = self.create_user(username="platform-admin")
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)

        self.assertTrue(
            IsPlatformSuperAdmin().has_permission(SimpleNamespace(user=user), view=None)
        )

    def test_required_fields_do_not_include_role(self):
        self.assertEqual(User.REQUIRED_FIELDS, ["email"])

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
            username="allowed-platform-admin", password="test-pass-123"
        )
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)

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
