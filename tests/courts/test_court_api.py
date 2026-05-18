from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtStaffAssignment, CourtWorkingHour


class CourtAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, role: str) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            role=role,
        )

    def create_club(self, name: str, **extra_fields) -> Club:
        data = {
            "name": name,
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

    def create_membership(self, club: Club, user: User, role: str):
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
        )

    def create_staff_assignment(self, court: Court, user: User, **extra_fields):
        return CourtStaffAssignment.objects.create(
            court=court,
            user=user,
            **extra_fields,
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


class CourtAPITests(CourtAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_user(
            "court-admin",
            User.Role.PLATFORM_SUPER_ADMIN,
        )
        self.owner = self.create_user("court-owner", User.Role.CLUB_OWNER)
        self.other_owner = self.create_user(
            "other-court-owner",
            User.Role.CLUB_OWNER,
        )
        self.manager = self.create_user("court-manager", User.Role.MANAGER)
        self.staff = self.create_user("court-staff", User.Role.STAFF)
        self.club = self.create_club("Court Club")
        self.other_club = self.create_club("Other Court Club")
        self.create_membership(self.club, self.owner, ClubMembership.Role.OWNER)
        self.create_membership(
            self.other_club,
            self.other_owner,
            ClubMembership.Role.OWNER,
        )
        self.create_membership(self.club, self.manager, ClubMembership.Role.MANAGER)

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def post_court(self, club, **extra_fields):
        data = {
            "club": club.id,
            "name": "Court 1",
            "sport_type": Court.SportType.FOOTBALL,
            "default_price": "300.00",
        }
        data.update(extra_fields)
        return self.client.post(reverse("court-list"), data, format="json")

    def test_platform_admin_can_create_court(self):
        self.authenticate_platform_admin()

        response = self.post_court(self.club)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        court = Court.objects.get(name="Court 1")
        self.assertEqual(court.created_by, self.platform_admin)

    def test_owner_can_create_court_inside_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_court(self.club, name="Owner Court")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_cannot_create_court_inside_unrelated_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_court(self.other_club, name="Blocked Court")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_create_court(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_court(self.club, name="Manager Court")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_create_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_court(self.club, name="Staff Court")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_court_default_price_cannot_be_negative(self):
        self.authenticate_platform_admin()

        response = self.post_court(self.club, default_price="-1.00")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_slot_duration_must_be_positive(self):
        self.authenticate_platform_admin()

        response = self.post_court(self.club, slot_duration_minutes=0)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_court_default_flags(self):
        court = self.create_court(self.club, "Default Court")

        self.assertEqual(court.internal_hold_expiry_hours, 12)
        self.assertFalse(court.requires_digital_payment_reference)

    def test_delete_court_is_not_allowed(self):
        court = self.create_court(self.club, "No Delete Court")
        self.authenticate_platform_admin()

        response = self.client.delete(reverse("court-detail", kwargs={"pk": court.pk}))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_court_can_be_deactivated_with_patch(self):
        court = self.create_court(self.club, "Deactivate Court")
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("court-detail", kwargs={"pk": court.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        court.refresh_from_db()
        self.assertFalse(court.is_active)

    def test_owner_can_list_courts_only_inside_owned_clubs(self):
        owned_court = self.create_court(self.club, "Owned Court")
        other_court = self.create_court(self.other_club, "Other Court")
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(reverse("court-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {owned_court.id})
        self.assertNotIn(other_court.id, self.list_ids(response))

    def test_manager_can_list_courts_only_inside_assigned_club(self):
        assigned_court = self.create_court(self.club, "Assigned Court")
        other_court = self.create_court(self.other_club, "Other Manager Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(reverse("court-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_court.id})
        self.assertNotIn(other_court.id, self.list_ids(response))

    def test_staff_can_see_assigned_court_only(self):
        assigned_court = self.create_court(self.club, "Staff Court")
        other_court = self.create_court(self.club, "Hidden Staff Court")
        self.create_staff_assignment(assigned_court, self.staff)
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(reverse("court-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_court.id})
        self.assertNotIn(other_court.id, self.list_ids(response))

    def test_manager_cannot_change_price_unless_club_allows(self):
        court = self.create_court(self.club, "Manager Price Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.patch(
            reverse("court-detail", kwargs={"pk": court.pk}),
            {"default_price": "400.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_change_price_if_club_allows(self):
        self.club.manager_can_change_pricing = True
        self.club.save(update_fields=["manager_can_change_pricing"])
        court = self.create_court(self.club, "Allowed Manager Price Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.patch(
            reverse("court-detail", kwargs={"pk": court.pk}),
            {"default_price": "400.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        court.refresh_from_db()
        self.assertEqual(str(court.default_price), "400.00")

    def test_staff_cannot_change_price(self):
        court = self.create_court(self.club, "Staff Price Court")
        self.create_staff_assignment(court, self.staff)
        self.client.force_authenticate(user=self.staff)

        response = self.client.patch(
            reverse("court-detail", kwargs={"pk": court.pk}),
            {"default_price": "400.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CourtWorkingHourAPITests(CourtAPITestCase):
    def setUp(self):
        self.owner = self.create_user("hours-owner", User.Role.CLUB_OWNER)
        self.other_owner = self.create_user("hours-other-owner", User.Role.CLUB_OWNER)
        self.staff = self.create_user("hours-staff", User.Role.STAFF)
        self.club = self.create_club("Hours Club")
        self.other_club = self.create_club("Other Hours Club")
        self.create_membership(self.club, self.owner, ClubMembership.Role.OWNER)
        self.create_membership(
            self.other_club,
            self.other_owner,
            ClubMembership.Role.OWNER,
        )
        self.court = self.create_court(self.club, "Hours Court")
        self.other_court = self.create_court(self.other_club, "Other Hours Court")

    def post_hours(self, court, **extra_fields):
        data = {
            "court": court.id,
            "weekday": CourtWorkingHour.Weekday.MONDAY,
            "opens_at": "10:00:00",
            "closes_at": "22:00:00",
            "is_closed": False,
        }
        data.update(extra_fields)
        return self.client.post(
            reverse("court-working-hour-list"),
            data,
            format="json",
        )

    def test_owner_can_create_working_hours_for_owned_court(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_duplicate_same_court_weekday(self):
        CourtWorkingHour.objects.create(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
            opens_at="10:00",
            closes_at="22:00",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_opens_at_must_be_before_closes_at(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(
            self.court,
            opens_at="22:00:00",
            closes_at="10:00:00",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_open_day_requires_open_and_close_times(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(
            self.court,
            opens_at=None,
            closes_at=None,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_cannot_manage_unrelated_court_working_hours(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_manage_working_hours(self):
        self.create_staff_assignment(self.court, self.staff)
        self.client.force_authenticate(user=self.staff)

        response = self.post_hours(self.court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CourtStaffAssignmentAPITests(CourtAPITestCase):
    def setUp(self):
        self.owner = self.create_user("assign-owner", User.Role.CLUB_OWNER)
        self.club_owner = self.create_user("assign-club-owner", User.Role.CLUB_OWNER)
        self.manager = self.create_user("assign-manager", User.Role.MANAGER)
        self.staff = self.create_user("assign-staff", User.Role.STAFF)
        self.other_staff = self.create_user("assign-other-staff", User.Role.STAFF)
        self.club = self.create_club("Assign Club")
        self.create_membership(self.club, self.owner, ClubMembership.Role.OWNER)
        self.court = self.create_court(self.club, "Assign Court")
        self.other_court = self.create_court(self.club, "Assign Other Court")

    def post_assignment(self, user, court=None):
        return self.client.post(
            reverse("court-staff-assignment-list"),
            {
                "court": (court or self.court).id,
                "user": user.id,
            },
            format="json",
        )

    def test_owner_can_assign_staff_to_owned_court(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_assignment(self.staff)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_assign_manager_as_court_staff(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_assignment(self.manager)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_assign_club_owner_as_court_staff(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_assignment(self.club_owner)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_assign_same_staff_to_multiple_active_courts(self):
        self.create_staff_assignment(self.court, self.staff)
        self.client.force_authenticate(user=self.owner)

        response = self.post_assignment(self.staff, court=self.other_court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_duplicate_active_staff_assignment(self):
        self.create_staff_assignment(self.court, self.staff)
        self.client.force_authenticate(user=self.owner)

        response = self.post_assignment(self.staff)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivated_assignment_no_longer_grants_staff_court_scope(self):
        assignment = self.create_staff_assignment(self.court, self.staff)
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(reverse("court-list"))
        self.assertEqual(self.list_ids(response), {self.court.id})

        self.client.force_authenticate(user=self.owner)
        deactivate_response = self.client.patch(
            reverse("court-staff-assignment-detail", kwargs={"pk": assignment.pk}),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(deactivate_response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.staff)
        response = self.client.get(reverse("court-list"))
        self.assertEqual(self.list_ids(response), set())

    def test_staff_can_retrieve_only_assigned_court(self):
        self.create_staff_assignment(self.court, self.staff)
        self.client.force_authenticate(user=self.staff)

        assigned_response = self.client.get(
            reverse("court-detail", kwargs={"pk": self.court.pk})
        )
        unrelated_response = self.client.get(
            reverse("court-detail", kwargs={"pk": self.other_court.pk})
        )

        self.assertEqual(assigned_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unrelated_response.status_code, status.HTTP_404_NOT_FOUND)
