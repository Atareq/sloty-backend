from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership


class ClubAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, role: str) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            role=role,
        )

    def create_club(self, name: str, created_by=None, **extra_fields) -> Club:
        data = {
            "name": name,
            "city": "Assiut",
            "area": "Downtown",
            "created_by": created_by,
        }
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_membership(
        self,
        club: Club,
        user: User,
        role: str,
        is_active: bool = True,
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            is_active=is_active,
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


class ClubAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_user(
            "platform-admin",
            User.Role.PLATFORM_SUPER_ADMIN,
        )
        self.owner = self.create_user("owner", User.Role.CLUB_OWNER)
        self.other_owner = self.create_user("other-owner", User.Role.CLUB_OWNER)
        self.manager = self.create_user("manager", User.Role.MANAGER)

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_platform_admin_can_create_club_through_api(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-list"),
            {
                "name": "El-Nasr Club",
                "city": "Assiut",
                "area": "West",
                "address": "Main street",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        club = Club.objects.get(name="El-Nasr Club")
        self.assertEqual(club.created_by, self.platform_admin)

    def test_club_defaults(self):
        club = self.create_club("Default Club")

        self.assertTrue(club.is_active)
        self.assertFalse(club.manager_can_settle_transactions)
        self.assertFalse(club.manager_can_change_pricing)

    def test_club_can_be_deactivated_with_patch(self):
        club = self.create_club("Deactivate Club")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("club-detail", kwargs={"pk": club.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        club.refresh_from_db()
        self.assertFalse(club.is_active)

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
                "city": "Assiut",
                "area": "East",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_list_only_assigned_clubs(self):
        owned_club = self.create_club("Owned Club")
        unrelated_club = self.create_club("Unrelated Club")
        self.create_membership(
            owned_club,
            self.owner,
            ClubMembership.Role.OWNER,
        )
        self.create_membership(
            unrelated_club,
            self.other_owner,
            ClubMembership.Role.OWNER,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {owned_club.id})

    def test_manager_can_list_only_assigned_club(self):
        assigned_club = self.create_club("Assigned Club")
        unrelated_club = self.create_club("Manager Unrelated Club")
        self.create_membership(
            assigned_club,
            self.manager,
            ClubMembership.Role.MANAGER,
        )
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_club.id})
        self.assertNotIn(unrelated_club.id, self.list_ids(response))

    def test_owner_cannot_retrieve_unrelated_club(self):
        unrelated_club = self.create_club("Hidden Club")
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            reverse("club-detail", kwargs={"pk": unrelated_club.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_cannot_retrieve_unrelated_club(self):
        unrelated_club = self.create_club("Hidden From Manager")
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            reverse("club-detail", kwargs={"pk": unrelated_club.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ClubMembershipAPITests(ClubAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_user(
            "membership-admin",
            User.Role.PLATFORM_SUPER_ADMIN,
        )
        self.club = self.create_club("Membership Club")
        self.other_club = self.create_club("Other Membership Club")
        self.owner = self.create_user("membership-owner", User.Role.CLUB_OWNER)
        self.manager = self.create_user("membership-manager", User.Role.MANAGER)
        self.staff = self.create_user("membership-staff", User.Role.STAFF)

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_platform_admin_can_assign_club_owner(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.owner.id,
                "role": ClubMembership.Role.OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ClubMembership.objects.filter(
                club=self.club,
                user=self.owner,
                role=ClubMembership.Role.OWNER,
                is_active=True,
            ).exists()
        )

    def test_platform_admin_can_assign_manager(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.manager.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_assign_staff_as_owner(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.staff.id,
                "role": ClubMembership.Role.OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_assign_staff_as_manager(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.staff.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_assign_club_owner_as_manager(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.owner.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_duplicate_active_owner_assignment(self):
        self.create_membership(self.club, self.owner, ClubMembership.Role.OWNER)
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.club.id,
                "user": self.owner.id,
                "role": ClubMembership.Role.OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_manager_cannot_have_active_memberships_in_multiple_clubs(self):
        self.create_membership(self.club, self.manager, ClubMembership.Role.MANAGER)
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("club-membership-list"),
            {
                "club": self.other_club.id,
                "user": self.manager.id,
                "role": ClubMembership.Role.MANAGER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivated_membership_no_longer_grants_scope(self):
        membership = self.create_membership(
            self.club,
            self.owner,
            ClubMembership.Role.OWNER,
        )
        self.client.force_authenticate(user=self.owner)
        scoped_response = self.client.get(reverse("club-list"))
        self.assertEqual(self.list_ids(scoped_response), {self.club.id})

        self.authenticate_platform_admin()
        response = self.client.patch(
            reverse("club-membership-detail", kwargs={"pk": membership.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.owner)
        scoped_response = self.client.get(reverse("club-list"))
        self.assertEqual(self.list_ids(scoped_response), set())
