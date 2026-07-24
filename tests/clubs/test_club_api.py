from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.access import ClubAccessContext
from apps.clubs.models import Club, ClubMembership
from apps.clubs.services import create_club_member
from apps.courts.models import Court


class ClubAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="platform-admin") -> User:
        return self.create_user(username=username, is_platform_admin=True)

    def create_club(self, name: str, slug: str | None = None, **extra_fields) -> Club:
        data = {
            "name": name,
            "governorate": "ASSIUT",
            "city": "ASSIUT_MARKAZ",
        }
        if slug is not None:
            data["slug"] = slug
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

    def create_membership(
        self,
        user: User,
        club: Club,
        role: str,
        court: Court | None = None,
        is_active: bool = True,
        **extra_fields,
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
            is_active=is_active,
            **extra_fields,
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def assert_field_error(self, response, field):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn(field, response.data["field_errors"])

    def assert_field_error_message(self, response, field, message):
        self.assert_field_error(response, field)
        self.assertEqual(
            response.data["field_errors"][field][0]["message"],
            message,
        )

    def membership_list_url(self, club):
        return reverse("club-membership-list", kwargs={"club_slug": club.slug})

    def membership_detail_url(self, club, membership):
        return reverse(
            "club-membership-detail",
            kwargs={"club_slug": club.slug, "pk": membership.pk},
        )

    def club_user_list_url(self, club):
        return reverse("club-user-list", kwargs={"club_slug": club.slug})


class ClubAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.owner = self.create_user("owner")
        self.other_owner = self.create_user("other-owner")
        self.manager = self.create_user("manager")
        self.staff = self.create_user("staff")

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_platform_admin_can_create_club_through_api(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "El-Nasr Club",
                "governorate": "ASSIUT",
                "city": "ASSIUT_MARKAZ",
                "address": "Main street",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        club = Club.objects.get(name="El-Nasr Club")
        self.assertEqual(club.created_by, self.platform_admin)
        self.assertEqual(club.slug, "el-nasr-club")
        self.assertEqual(club.governorate, "ASSIUT")
        self.assertEqual(club.city, "ASSIUT_MARKAZ")
        self.assertEqual(response.data["slug"], "el-nasr-club")
        self.assertEqual(response.data["governorate"], "ASSIUT")
        self.assertEqual(response.data["city"], "ASSIUT_MARKAZ")

    def test_create_club_with_invalid_governorate_fails(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "Invalid Governorate Club",
                "governorate": "UNKNOWN",
                "city": "ASSIUT_MARKAZ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "governorate")

    def test_create_club_with_invalid_city_fails(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "Invalid City Club",
                "governorate": "ASSIUT",
                "city": "Assiut",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "city")

    def test_create_club_with_city_from_another_governorate_fails(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "Wrong Governorate City Club",
                "governorate": "ASSIUT",
                "city": "SOHAG_MARKAZ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error_message(
            response,
            "city",
            "City must belong to the selected governorate.",
        )

    def test_club_slug_can_be_provided_on_create(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "Custom Slug Club",
                "slug": "custom-club",
                "governorate": "ASSIUT",
                "city": "ASSIUT_MARKAZ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["slug"], "custom-club")

    def test_club_slug_is_unique(self):
        self.create_club("Existing Club", slug="existing-club")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_club("Duplicate Club", slug="existing-club")

    def test_club_slug_is_generated_uniquely_when_missing(self):
        first = self.create_club("Repeated Club")
        second = self.create_club("Repeated Club")

        self.assertEqual(first.slug, "repeated-club")
        self.assertEqual(second.slug, "repeated-club-2")

    def test_club_defaults(self):
        club = self.create_club("Default Club")

        self.assertTrue(club.is_active)
        self.assertFalse(
            hasattr(club, "manager_can_settle_transactions"),
        )
        self.assertFalse(hasattr(club, "manager_can_change_pricing"))

    def test_club_can_be_deactivated_with_patch(self):
        club = self.create_club("Deactivate Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"is_active": False, "slug": "changed-slug"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        club.refresh_from_db()
        self.assertFalse(club.is_active)
        self.assertNotEqual(club.slug, "changed-slug")

    def test_update_city_to_valid_city_succeeds(self):
        club = self.create_club("City Update Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"city": "ASSIUT_1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        club.refresh_from_db()
        self.assertEqual(club.city, "ASSIUT_1")
        self.assertEqual(response.data["city"], "ASSIUT_1")

    def test_update_city_to_city_from_another_governorate_fails(self):
        club = self.create_club("Invalid City Update Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"city": "SOHAG_MARKAZ"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error_message(
            response,
            "city",
            "City must belong to the selected governorate.",
        )

    def test_partial_update_governorate_validates_existing_city(self):
        club = self.create_club("Governorate Partial Update Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"governorate": "SOHAG"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error_message(
            response,
            "city",
            "City must belong to the selected governorate.",
        )

    def test_partial_update_city_validates_existing_governorate(self):
        club = self.create_club("City Partial Update Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"city": "SOHAG_MARKAZ"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error_message(
            response,
            "city",
            "City must belong to the selected governorate.",
        )

    def test_delete_club_is_not_allowed(self):
        club = self.create_club("No Delete Club")
        self.authenticate_platform_admin()

        response = self.client.delete(reverse("club-detail", kwargs={"pk": club.pk}))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_anonymous_cannot_access_clubs(self):
        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_platform_user_cannot_create_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "Owner Created Club",
                "governorate": "ASSIUT",
                "city": "ASSIUT_MARKAZ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_admin_can_list_all_clubs(self):
        first = self.create_club("First Club")
        second = self.create_club("Second Club")
        self.authenticate_platform_admin()

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {first.id, second.id})

    def test_owner_can_list_only_assigned_clubs(self):
        owned_club = self.create_club("Owned Club")
        unrelated_club = self.create_club("Unrelated Club")
        self.create_membership(self.owner, owned_club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.other_owner,
            unrelated_club,
            ClubMembership.Role.OWNER,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {owned_club.id})

    def test_manager_can_list_only_assigned_club(self):
        assigned_club = self.create_club("Assigned Club")
        unrelated_club = self.create_club("Manager Unrelated Club")
        self.create_membership(self.manager, assigned_club, ClubMembership.Role.MANAGER)
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_club.id})
        self.assertNotIn(unrelated_club.id, self.list_ids(response))

    def test_staff_can_list_club_through_staff_membership(self):
        club = self.create_club("Staff Club")
        court = self.create_court(club, "Staff Court")
        self.create_membership(
            self.staff,
            club,
            ClubMembership.Role.STAFF,
            court=court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {club.id})

    def test_owner_can_update_owned_club(self):
        club = self.create_club("Owned Update Club")
        self.create_membership(self.owner, club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"notes": "Updated by owner"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        club.refresh_from_db()
        self.assertEqual(club.notes, "Updated by owner")

    def test_user_cannot_access_unrelated_club(self):
        unrelated_club = self.create_club("Hidden Club")
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            reverse("club-detail", kwargs={"pk": unrelated_club.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ClubMembershipAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("membership-admin")
        self.club = self.create_club("Membership Club", slug="membership-club")
        self.other_club = self.create_club(
            "Other Membership Club",
            slug="other-membership-club",
        )
        self.court = self.create_court(self.club, "Membership Court")
        self.other_court = self.create_court(self.other_club, "Other Court")
        self.owner = self.create_user("membership-owner")
        self.manager = self.create_user("membership-manager")
        self.staff = self.create_user("membership-staff")
        self.other_user = self.create_user("membership-other-user")

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def post_membership(self, club, user, role, court=None, **extra_fields):
        data = {
            "user": user.id,
            "role": role,
            "is_active": True,
        }
        if court is not None:
            data["court"] = court.id
        data.update(extra_fields)
        return self.client.post(self.membership_list_url(club), data, format="json")

    def nested_user_payload(self, username, **extra_fields):
        data = {
            "username": username,
            "email": f"{username}@example.com",
            "password": self.password,
            "first_name": "Nested",
            "last_name": "User",
            "phone_number": "+201000000010",
        }
        data.update(extra_fields)
        return data

    def post_nested_membership(self, club, role, username, court=None, **extra_fields):
        data = {
            "user": self.nested_user_payload(username),
            "role": role,
        }
        if court is not None:
            data["court"] = court.id
        data.update(extra_fields)
        return self.client.post(self.membership_list_url(club), data, format="json")

    def test_platform_admin_can_assign_owner_manager_and_staff(self):
        self.authenticate_platform_admin()

        owner_response = self.post_membership(
            self.club,
            self.owner,
            ClubMembership.Role.OWNER,
        )
        manager_response = self.post_membership(
            self.club,
            self.manager,
            ClubMembership.Role.MANAGER,
        )
        staff_response = self.post_membership(
            self.club,
            self.staff,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

        self.assertEqual(owner_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(manager_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(staff_response.status_code, status.HTTP_201_CREATED)

    def test_platform_admin_can_create_owner_with_nested_user_payload(self):
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.OWNER,
            "nested-owner",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="nested-owner")
        membership = ClubMembership.objects.get(user=user, club=self.club)
        self.assertEqual(membership.role, ClubMembership.Role.OWNER)
        self.assertTrue(membership.is_active)
        self.assertFalse(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(self.password))
        self.assertEqual(response.data["user"], user.id)
        self.assertEqual(response.data["user_summary"]["username"], "nested-owner")
        self.assertNotIn("password", response.data)
        self.assertNotIn("password", response.data["user_summary"])

    def test_platform_admin_can_create_manager_with_nested_user_payload(self):
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.MANAGER,
            "nested-manager",
            manager_can_settle_transactions=True,
            manager_can_change_pricing=True,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="nested-manager")
        membership = ClubMembership.objects.get(user=user, club=self.club)
        self.assertEqual(membership.role, ClubMembership.Role.MANAGER)
        self.assertIsNone(membership.court)
        self.assertTrue(membership.manager_can_settle_transactions)
        self.assertTrue(membership.manager_can_change_pricing)
        self.assertTrue(response.data["manager_can_settle_transactions"])
        self.assertTrue(response.data["manager_can_change_pricing"])

    def test_non_manager_membership_cannot_enable_manager_permissions(self):
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.club,
            self.owner,
            ClubMembership.Role.OWNER,
            manager_can_settle_transactions=True,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "manager_permissions")

    def test_owner_can_update_manager_permission_flags(self):
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        manager_membership = self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            self.membership_detail_url(self.club, manager_membership),
            {
                "manager_can_settle_transactions": True,
                "manager_can_change_pricing": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        manager_membership.refresh_from_db()
        self.assertTrue(manager_membership.manager_can_settle_transactions)
        self.assertFalse(manager_membership.manager_can_change_pricing)
        self.assertTrue(response.data["manager_can_settle_transactions"])
        self.assertFalse(response.data["manager_can_change_pricing"])

    def test_platform_admin_can_create_staff_with_nested_user_payload_and_court(self):
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.STAFF,
            "nested-staff",
            court=self.court,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="nested-staff")
        membership = ClubMembership.objects.get(user=user, club=self.club)
        self.assertEqual(membership.role, ClubMembership.Role.STAFF)
        self.assertEqual(membership.court, self.court)
        self.assertEqual(response.data["court"], self.court.id)

    def test_owner_can_create_manager_and_staff_inside_owned_club(self):
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)

        manager_response = self.post_membership(
            self.club,
            self.manager,
            ClubMembership.Role.MANAGER,
        )
        staff_response = self.post_membership(
            self.club,
            self.staff,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

        self.assertEqual(manager_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(staff_response.status_code, status.HTTP_201_CREATED)

    def test_owner_can_create_manager_and_staff_with_nested_user_payload(self):
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)

        manager_response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.MANAGER,
            "owner-created-manager",
        )
        staff_response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.STAFF,
            "owner-created-staff",
            court=self.court,
        )

        self.assertEqual(manager_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(staff_response.status_code, status.HTTP_201_CREATED)

    def test_owner_cannot_create_owner_membership(self):
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.client.force_authenticate(user=self.owner)

        response = self.post_membership(
            self.club,
            self.other_user,
            ClubMembership.Role.OWNER,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_create_memberships(self):
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.client.force_authenticate(user=self.manager)

        response = self.post_membership(
            self.club,
            self.staff,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_create_memberships(self):
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.post_membership(
            self.club,
            self.manager,
            ClubMembership.Role.MANAGER,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_and_manager_require_no_court(self):
        self.authenticate_platform_admin()

        owner_response = self.post_membership(
            self.club,
            self.owner,
            ClubMembership.Role.OWNER,
            court=self.court,
        )
        manager_response = self.post_membership(
            self.club,
            self.manager,
            ClubMembership.Role.MANAGER,
            court=self.court,
        )

        self.assertEqual(owner_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(manager_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_requires_court(self):
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.club,
            self.staff,
            ClubMembership.Role.STAFF,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_staff_without_court_does_not_create_user(self):
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.STAFF,
            "staff-without-court",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "court")
        self.assertFalse(User.objects.filter(username="staff-without-court").exists())

    def test_staff_court_must_belong_to_url_club(self):
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.club,
            self.staff,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_staff_with_other_club_court_does_not_create_user(self):
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.STAFF,
            "staff-other-club-court",
            court=self.other_court,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            User.objects.filter(username="staff-other-club-court").exists()
        )

    def test_duplicate_active_memberships_are_rejected(self):
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.club,
            self.owner,
            ClubMembership.Role.OWNER,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_nested_username_is_rejected(self):
        self.create_user("duplicate-nested-user")
        self.authenticate_platform_admin()

        response = self.post_nested_membership(
            self.club,
            ClubMembership.Role.MANAGER,
            "duplicate-nested-user",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "user")

    def test_existing_user_can_be_attached_with_user_id(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            self.membership_list_url(self.club),
            {
                "user_id": self.other_user.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membership = ClubMembership.objects.get(user=self.other_user, club=self.club)
        self.assertEqual(membership.role, ClubMembership.Role.MANAGER)

    def test_platform_admin_existing_user_cannot_be_attached_as_club_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            self.membership_list_url(self.club),
            {
                "user_id": self.platform_admin.id,
                "role": ClubMembership.Role.OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "user_id")

    def test_membership_creation_requires_exactly_one_user_source(self):
        self.authenticate_platform_admin()

        missing_response = self.client.post(
            self.membership_list_url(self.club),
            {"role": ClubMembership.Role.MANAGER},
            format="json",
        )
        both_response = self.client.post(
            self.membership_list_url(self.club),
            {
                "user": self.nested_user_payload("two-user-sources"),
                "user_id": self.other_user.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(both_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_club_member_rolls_back_user_when_membership_create_fails(self):
        request = SimpleNamespace(user=self.platform_admin)
        access = ClubAccessContext(request=request, club=self.club)

        with patch(
            "apps.clubs.services.ClubMembership.objects.create"
        ) as mocked_create:
            mocked_create.side_effect = RuntimeError("membership create failed")
            with self.assertRaises(RuntimeError):
                create_club_member(
                    access=access,
                    role=ClubMembership.Role.MANAGER,
                    created_by=self.platform_admin,
                    user_data=self.nested_user_payload("rollback-user"),
                )

        self.assertFalse(User.objects.filter(username="rollback-user").exists())

    def test_manager_can_have_only_one_active_club_membership(self):
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.other_club,
            self.manager,
            ClubMembership.Role.MANAGER,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_have_only_one_active_court_membership(self):
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.authenticate_platform_admin()

        response = self.post_membership(
            self.other_club,
            self.staff,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivated_membership_removes_scope(self):
        membership = self.create_membership(
            self.owner,
            self.club,
            ClubMembership.Role.OWNER,
        )
        self.client.force_authenticate(user=self.owner)
        scoped_response = self.client.get(reverse("club-list"))
        self.assertEqual(self.list_ids(scoped_response), {self.club.id})

        self.authenticate_platform_admin()
        response = self.client.patch(
            self.membership_detail_url(self.club, membership),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.owner)
        scoped_response = self.client.get(reverse("club-list"))
        self.assertEqual(self.list_ids(scoped_response), set())


class ClubUserListAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("club-users-admin")
        self.owner = self.create_user("club-users-owner")
        self.other_owner = self.create_user("club-users-other-owner")
        self.manager = self.create_user("club-users-manager")
        self.restricted_manager = self.create_user("club-users-restricted-manager")
        self.staff = self.create_user(
            "staff-ahmed",
            first_name="Ahmed",
            last_name="Ali",
            phone_number="+201000000099",
        )
        self.inactive_staff = self.create_user("club-users-inactive-staff")
        self.other_staff = self.create_user("club-users-other-staff")
        self.club = self.create_club("Club Users Club", slug="club-users")
        self.restricted_club = self.create_club(
            "Restricted Club Users Club",
            slug="restricted-club-users",
        )
        self.other_club = self.create_club(
            "Other Club Users Club",
            slug="other-club-users",
        )
        self.court = self.create_court(self.club, "Court A")
        self.other_court = self.create_court(self.club, "Court B")
        self.external_court = self.create_court(self.other_club, "External Court")
        self.owner_membership = self.create_membership(
            self.owner,
            self.club,
            ClubMembership.Role.OWNER,
        )
        self.manager_membership = self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
        )
        self.restricted_manager_membership = self.create_membership(
            self.restricted_manager,
            self.restricted_club,
            ClubMembership.Role.MANAGER,
        )
        self.staff_membership = self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.inactive_membership = self.create_membership(
            self.inactive_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
            is_active=False,
        )
        self.other_membership = self.create_membership(
            self.other_staff,
            self.other_club,
            ClubMembership.Role.STAFF,
            court=self.external_court,
        )
        self.create_membership(
            self.other_owner,
            self.other_club,
            ClubMembership.Role.OWNER,
        )

    def test_unauthenticated_user_cannot_list_club_users(self):
        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_list_users_in_any_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.club_user_list_url(self.other_club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.other_owner.id, self.other_staff.id},
        )

    def test_owner_can_list_users_in_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {
                self.owner.id,
                self.manager.id,
                self.staff.id,
                self.inactive_staff.id,
            },
        )

    def test_owner_cannot_list_users_in_another_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.club_user_list_url(self.other_club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_with_settlement_permission_can_list_active_employees_only(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.manager.id, self.staff.id})
        self.assertNotIn(self.owner.id, self.list_ids(response))
        self.assertNotIn(self.inactive_staff.id, self.list_ids(response))
        self.assertNotIn(self.other_staff.id, self.list_ids(response))

    def test_manager_without_settlement_permission_can_list_active_employees(self):
        self.client.force_authenticate(user=self.restricted_manager)

        response = self.client.get(self.club_user_list_url(self.restricted_club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.restricted_manager.id})

    def test_staff_cannot_list_club_users(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_filter_returns_only_selected_role(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.staff.id, self.inactive_staff.id},
        )

    def test_court_filter_returns_only_users_assigned_to_court(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(self.list_ids(response), {self.staff.id})

    def test_role_and_court_filter_work_together(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF, "court": self.court.id},
        )

        self.assertEqual(self.list_ids(response), {self.staff.id})

    def test_is_active_filter_works(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"is_active": "false"},
        )

        self.assertEqual(self.list_ids(response), {self.inactive_staff.id})

    def test_search_filter_works(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"search": "ahmed"},
        )

        self.assertEqual(self.list_ids(response), {self.staff.id})

    def test_manager_role_owner_filter_returns_empty(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.OWNER},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), set())

    def test_manager_inactive_filter_returns_empty(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"is_active": "false"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), set())

    def test_manager_can_filter_by_court_and_search_employees(self):
        self.client.force_authenticate(user=self.manager)

        court_response = self.client.get(
            self.club_user_list_url(self.club),
            {"court": self.court.id},
        )
        search_response = self.client.get(
            self.club_user_list_url(self.club),
            {"search": "ahmed"},
        )

        self.assertEqual(self.list_ids(court_response), {self.staff.id})
        self.assertEqual(self.list_ids(search_response), {self.staff.id})

    def test_response_includes_identity_and_membership_fields(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF, "court": self.court.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = response.data["results"][0]
        self.assertEqual(item["id"], self.staff.id)
        self.assertEqual(item["membership_id"], self.staff_membership.id)
        self.assertEqual(item["username"], self.staff.username)
        self.assertEqual(item["first_name"], "Ahmed")
        self.assertEqual(item["last_name"], "Ali")
        self.assertEqual(item["phone_number"], "+201000000099")
        self.assertEqual(item["role"], ClubMembership.Role.STAFF)
        self.assertEqual(item["club"], self.club.id)
        self.assertEqual(item["club_slug"], self.club.slug)
        self.assertEqual(item["court"], self.court.id)
        self.assertEqual(item["court_name"], self.court.name)
        self.assertTrue(item["membership_is_active"])

    def test_user_model_does_not_gain_club_role_or_court_fields(self):
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)


class ClubUserListResponseFieldAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("club-users-admin")
        self.owner = self.create_user("club-users-owner")
        self.other_owner = self.create_user("club-users-other-owner")
        self.manager = self.create_user(
            "club-users-manager",
            first_name="Mona",
            last_name="Manager",
            phone_number="+201000000101",
        )
        self.staff = self.create_user(
            "club-users-staff-ahmed",
            first_name="Ahmed",
            last_name="Ali",
            phone_number="+201000000102",
        )
        self.other_staff = self.create_user(
            "club-users-staff-other",
            first_name="Omar",
            last_name="Other",
            phone_number="+201000000103",
        )
        self.inactive_staff = self.create_user(
            "club-users-inactive",
            first_name="Inactive",
            phone_number="+201000000104",
        )
        self.club = self.create_club("Club Users Club", slug="club-users")
        self.other_club = self.create_club(
            "Other Club Users Club",
            slug="other-club-users",
        )
        self.court = self.create_court(self.club, "Court A")
        self.other_court = self.create_court(self.club, "Court B")
        self.other_club_court = self.create_court(self.other_club, "Other Court")
        self.owner_membership = self.create_membership(
            self.owner,
            self.club,
            ClubMembership.Role.OWNER,
        )
        self.manager_membership = self.create_membership(
            self.manager,
            self.club,
            ClubMembership.Role.MANAGER,
            manager_can_settle_transactions=True,
            manager_can_change_pricing=True,
        )
        self.staff_membership = self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.other_staff_membership = self.create_membership(
            self.other_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.other_court,
        )
        self.inactive_staff_membership = self.create_membership(
            self.inactive_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
            is_active=False,
        )
        self.other_owner_membership = self.create_membership(
            self.other_owner,
            self.other_club,
            ClubMembership.Role.OWNER,
        )

    def test_unauthenticated_user_cannot_list_club_users(self):
        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_list_users_in_any_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        selected_club_response = self.client.get(self.club_user_list_url(self.club))
        other_club_response = self.client.get(self.club_user_list_url(self.other_club))

        self.assertEqual(selected_club_response.status_code, status.HTTP_200_OK)
        self.assertEqual(other_club_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(other_club_response),
            {self.other_owner.id},
        )

    def test_owner_can_list_owned_club_users(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.staff.id, self.list_ids(response))

    def test_owner_cannot_list_other_club_users(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.club_user_list_url(self.other_club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_with_settlement_permission_can_list_active_employees(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.manager.id, self.staff.id, self.other_staff.id},
        )
        self.assertNotIn(self.owner.id, self.list_ids(response))
        self.assertNotIn(self.inactive_staff.id, self.list_ids(response))

    def test_staff_cannot_list_club_users(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_filter_returns_only_selected_role(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.staff.id, self.other_staff.id, self.inactive_staff.id},
        )

    def test_court_filter_returns_only_users_assigned_to_court(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.staff.id, self.inactive_staff.id},
        )

    def test_role_and_court_filter_work_together(self):
        self.client.force_authenticate(user=self.platform_admin)

        staff_response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF, "court": self.court.id},
        )
        manager_response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.MANAGER, "court": self.court.id},
        )

        self.assertEqual(
            self.list_ids(staff_response),
            {self.staff.id, self.inactive_staff.id},
        )
        self.assertEqual(self.list_ids(manager_response), set())

    def test_is_active_filter_uses_membership_active_state(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"is_active": "false"},
        )

        self.assertEqual(self.list_ids(response), {self.inactive_staff.id})

    def test_search_filter_matches_user_identity_fields(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"search": "ahmed"},
        )

        self.assertEqual(self.list_ids(response), {self.staff.id})

    def test_users_from_another_club_do_not_leak(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.club_user_list_url(self.club))

        self.assertNotIn(self.other_owner.id, self.list_ids(response))

    def test_response_includes_user_identity_and_membership_fields(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(
            self.club_user_list_url(self.club),
            {"role": ClubMembership.Role.STAFF, "court": self.court.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = response.data["results"][0]
        self.assertEqual(item["id"], self.staff.id)
        self.assertEqual(item["membership_id"], self.staff_membership.id)
        self.assertEqual(item["username"], self.staff.username)
        self.assertEqual(item["first_name"], "Ahmed")
        self.assertEqual(item["phone_number"], "+201000000102")
        self.assertEqual(item["role"], ClubMembership.Role.STAFF)
        self.assertEqual(item["club"], self.club.id)
        self.assertEqual(item["club_slug"], self.club.slug)
        self.assertEqual(item["court"], self.court.id)
        self.assertEqual(item["court_name"], self.court.name)
        self.assertTrue(item["membership_is_active"])
        self.assertFalse(item["can_change_pricing"])
        self.assertFalse(item["can_manage_working_hours"])
        self.assertFalse(item["can_manage_settlements"])

    def test_no_user_scope_fields_were_added(self):
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)
