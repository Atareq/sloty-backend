from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.permissions import IsPlatformSuperAdmin
from apps.profiles.models import AdminProfile, Profile
from tests.profile_factories import create_user


class UserModelTests(TestCase):
    def test_authentication_account_can_exist_before_profile_provisioning(self):
        user = create_user(password="test-pass-123")
        self.assertTrue(user.check_password("test-pass-123"))
        self.assertNotIn("role", {field.name for field in User._meta.local_fields})
        self.assertNotIn("created_by", {field.name for field in User._meta.fields})
        self.assertFalse(hasattr(user, "role"))
        self.assertFalse(hasattr(user, "is_platform_super_admin"))

    def test_admin_profile_controls_platform_permission(self):
        user = create_user()
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)

        self.assertTrue(
            IsPlatformSuperAdmin().has_permission(SimpleNamespace(user=user), view=None)
        )

    def test_username_field_is_phone_number(self):
        self.assertEqual(User.USERNAME_FIELD, "phone_number")
        self.assertEqual(User.REQUIRED_FIELDS, ["email"])
        self.assertFalse(User._meta.get_field("phone_number").null)
        self.assertFalse(User._meta.get_field("phone_number").blank)

    def test_create_user_requires_phone_and_email(self):
        with self.assertRaises(TypeError):
            User.objects.create_user(email="missing-phone@example.com", password="x")
        with self.assertRaises(ValueError):
            User.objects.create_user(
                phone_number="+201011111111",
                email="",
                password="x",
            )

    def test_phone_number_is_normalized_and_unique(self):
        user = User.objects.create_user(
            phone_number="01011111111",
            email="one@example.com",
            password="test-pass-123",
        )
        self.assertEqual(str(user.phone_number), "+201011111111")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                phone_number="+201011111111",
                email="two@example.com",
                password="test-pass-123",
            )

    def test_email_is_normalized(self):
        user = User.objects.create_user(
            phone_number="+201013333333",
            email="Ada@Example.COM",
            password="test-pass-123",
        )
        self.assertEqual(user.email, "Ada@example.com")

    def test_create_superuser_flags(self):
        user = User.objects.create_superuser(
            phone_number="+201012222222",
            email="super@example.com",
            password="test-pass-123",
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("test-pass-123"))

    def test_display_name_uses_full_name_then_phone(self):
        user = create_user(first_name="Ada", last_name="Lovelace")
        self.assertEqual(user.get_display_name(), "Ada Lovelace")
        unnamed = create_user()
        self.assertEqual(unnamed.get_display_name(), str(unnamed.phone_number))


class PlatformPermissionTests(TestCase):
    def make_request(self, user):
        return SimpleNamespace(user=user)

    def test_permission_allows_platform_admin(self):
        user = create_user()
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)

        self.assertTrue(
            IsPlatformSuperAdmin().has_permission(self.make_request(user), view=None)
        )

    def test_permission_rejects_normal_user(self):
        user = create_user()

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
