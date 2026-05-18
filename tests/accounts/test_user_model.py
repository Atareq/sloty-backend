from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.permissions import (
    IsClubOwner,
    IsManager,
    IsPlatformSuperAdmin,
    IsStaffMember,
)


class UserRoleTests(TestCase):
    def create_user(self, username: str, role: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password="test-pass-123",
            role=role,
            **extra_fields,
        )

    def test_can_create_user_with_each_role(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                user = self.create_user(username=f"user-{role}", role=role)

                self.assertEqual(user.role, role)

    def test_role_helper_methods_match_role(self):
        cases = (
            (
                User.Role.PLATFORM_SUPER_ADMIN,
                "is_platform_super_admin",
            ),
            (User.Role.CLUB_OWNER, "is_club_owner"),
            (User.Role.MANAGER, "is_manager"),
            (User.Role.STAFF, "is_staff_member"),
        )
        helper_names = [helper_name for _, helper_name in cases]

        for role, active_helper_name in cases:
            with self.subTest(role=role):
                user = self.create_user(username=f"helper-{role}", role=role)

                for helper_name in helper_names:
                    helper = getattr(user, helper_name)
                    self.assertEqual(
                        helper(),
                        helper_name == active_helper_name,
                    )

    def test_created_by_can_be_null(self):
        user = self.create_user(
            username="created-by-null",
            role=User.Role.STAFF,
            created_by=None,
        )

        self.assertIsNone(user.created_by)

    def test_created_by_can_point_to_another_user(self):
        creator = self.create_user(
            username="creator",
            role=User.Role.PLATFORM_SUPER_ADMIN,
        )

        user = self.create_user(
            username="created-by-user",
            role=User.Role.STAFF,
            created_by=creator,
        )

        self.assertEqual(user.created_by, creator)

    def test_phone_number_allows_null_and_blank(self):
        field = User._meta.get_field("phone_number")

        self.assertTrue(field.null)
        self.assertTrue(field.blank)

        null_phone_user = self.create_user(
            username="null-phone",
            role=User.Role.MANAGER,
            phone_number=None,
        )
        blank_phone_user = self.create_user(
            username="blank-phone",
            role=User.Role.MANAGER,
            phone_number="",
        )

        self.assertIsNone(null_phone_user.phone_number)
        self.assertEqual(blank_phone_user.phone_number, "")


class RolePermissionTests(TestCase):
    permission_cases = (
        (User.Role.PLATFORM_SUPER_ADMIN, IsPlatformSuperAdmin),
        (User.Role.CLUB_OWNER, IsClubOwner),
        (User.Role.MANAGER, IsManager),
        (User.Role.STAFF, IsStaffMember),
    )

    def create_user(self, username: str, role: str) -> User:
        return User.objects.create_user(
            username=username,
            password="test-pass-123",
            role=role,
        )

    def make_request(self, user):
        return SimpleNamespace(user=user)

    def test_permissions_allow_matching_authenticated_role(self):
        for role, permission_class in self.permission_cases:
            with self.subTest(role=role):
                user = self.create_user(
                    username=f"allowed-{role}",
                    role=role,
                )

                self.assertTrue(
                    permission_class().has_permission(
                        self.make_request(user),
                        view=None,
                    )
                )

    def test_permissions_reject_non_matching_authenticated_role(self):
        for role, permission_class in self.permission_cases:
            denied_role = next(
                candidate for candidate in User.Role.values if candidate != role
            )

            with self.subTest(role=role, denied_role=denied_role):
                user = self.create_user(
                    username=f"denied-{role}",
                    role=denied_role,
                )

                self.assertFalse(
                    permission_class().has_permission(
                        self.make_request(user),
                        view=None,
                    )
                )

    def test_permissions_reject_anonymous_users(self):
        for role, permission_class in self.permission_cases:
            with self.subTest(role=role):
                self.assertFalse(
                    permission_class().has_permission(
                        self.make_request(AnonymousUser()),
                        view=None,
                    )
                )
