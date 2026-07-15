from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour


class CourtAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="court-admin") -> User:
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
    ) -> ClubMembership:
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
        )

    def court_list_url(self, club):
        return reverse("club-court-list", kwargs={"club_slug": club.slug})

    def court_detail_url(self, club, court):
        return reverse(
            "club-court-detail",
            kwargs={"club_slug": club.slug, "pk": court.pk},
        )

    def working_hour_list_url(self, club):
        return reverse(
            "club-court-working-hour-list",
            kwargs={"club_slug": club.slug},
        )

    def working_hour_detail_url(self, club, working_hour):
        return reverse(
            "club-court-working-hour-detail",
            kwargs={"club_slug": club.slug, "pk": working_hour.pk},
        )

    def nested_working_hour_url(self, club, court):
        return reverse(
            "club-court-nested-working-hour-list",
            kwargs={"club_slug": club.slug, "court_id": court.pk},
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


class CourtAPITests(CourtAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.owner = self.create_user("court-owner")
        self.other_owner = self.create_user("other-court-owner")
        self.manager = self.create_user("court-manager")
        self.staff = self.create_user("court-staff")
        self.club = self.create_club("Court Club", slug="court-club")
        self.other_club = self.create_club("Other Court Club", slug="other-court-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.other_owner,
            self.other_club,
            ClubMembership.Role.OWNER,
        )
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def post_court(self, club, **extra_fields):
        data = {
            "name": "Court 1",
            "sport_type": Court.SportType.FOOTBALL,
            "default_price": "300.00",
        }
        data.update(extra_fields)
        return self.client.post(self.court_list_url(club), data, format="json")

    def test_club_scoped_courts_route_works(self):
        court = self.create_court(self.club, "Route Court")
        self.authenticate_platform_admin()

        response = self.client.get(self.court_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {court.id})

    def test_platform_admin_can_create_court(self):
        self.authenticate_platform_admin()

        response = self.post_court(self.club)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        court = Court.objects.get(name="Court 1")
        self.assertEqual(court.club, self.club)
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
        court = self.create_court(self.club, "Staff Assigned Court")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=court,
        )
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

        response = self.client.delete(self.court_detail_url(self.club, court))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_court_can_be_deactivated_with_patch(self):
        court = self.create_court(self.club, "Deactivate Court")
        self.authenticate_platform_admin()

        response = self.client.patch(
            self.court_detail_url(self.club, court),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        court.refresh_from_db()
        self.assertFalse(court.is_active)

    def test_owner_can_list_courts_only_inside_selected_owned_club(self):
        owned_court = self.create_court(self.club, "Owned Court")
        other_court = self.create_court(self.other_club, "Other Court")
        self.client.force_authenticate(user=self.owner)

        owned_response = self.client.get(self.court_list_url(self.club))
        unrelated_response = self.client.get(self.court_list_url(self.other_club))

        self.assertEqual(owned_response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(owned_response), {owned_court.id})
        self.assertEqual(unrelated_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn(other_court.id, self.list_ids(owned_response))

    def test_manager_can_list_courts_inside_assigned_club(self):
        assigned_court = self.create_court(self.club, "Assigned Court")
        other_court = self.create_court(self.other_club, "Other Manager Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.court_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_court.id})
        self.assertNotIn(other_court.id, self.list_ids(response))

    def test_staff_can_see_assigned_court_only(self):
        assigned_court = self.create_court(self.club, "Staff Court")
        other_court = self.create_court(self.club, "Hidden Staff Court")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=assigned_court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.court_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {assigned_court.id})
        self.assertNotIn(other_court.id, self.list_ids(response))

    def test_manager_cannot_change_price_unless_club_allows(self):
        court = self.create_court(self.club, "Manager Price Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.patch(
            self.court_detail_url(self.club, court),
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
            self.court_detail_url(self.club, court),
            {"default_price": "400.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        court.refresh_from_db()
        self.assertEqual(str(court.default_price), "400.00")

    def test_manager_cannot_update_non_price_fields(self):
        self.club.manager_can_change_pricing = True
        self.club.save(update_fields=["manager_can_change_pricing"])
        court = self.create_court(self.club, "Manager Non Price Court")
        self.client.force_authenticate(user=self.manager)

        response = self.client.patch(
            self.court_detail_url(self.club, court),
            {"name": "Blocked Name"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_update_courts(self):
        court = self.create_court(self.club, "Staff Price Court")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.patch(
            self.court_detail_url(self.club, court),
            {"default_price": "400.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_retrieve_only_assigned_court(self):
        assigned_court = self.create_court(self.club, "Assigned Detail Court")
        other_court = self.create_court(self.club, "Other Detail Court")
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=assigned_court,
        )
        self.client.force_authenticate(user=self.staff)

        assigned_response = self.client.get(
            self.court_detail_url(self.club, assigned_court)
        )
        unrelated_response = self.client.get(
            self.court_detail_url(self.club, other_court)
        )

        self.assertEqual(assigned_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unrelated_response.status_code, status.HTTP_404_NOT_FOUND)


class CourtWorkingHourAPITests(CourtAPITestCase):
    def setUp(self):
        self.owner = self.create_user("hours-owner")
        self.manager = self.create_user("hours-manager")
        self.other_owner = self.create_user("hours-other-owner")
        self.staff = self.create_user("hours-staff")
        self.club = self.create_club("Hours Club", slug="hours-club")
        self.other_club = self.create_club("Other Hours Club", slug="other-hours-club")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.other_owner,
            self.other_club,
            ClubMembership.Role.OWNER,
        )
        self.court = self.create_court(self.club, "Hours Court")
        self.other_court = self.create_court(self.other_club, "Other Hours Court")

    def post_hours(self, club, court, **extra_fields):
        data = {
            "court": court.id,
            "weekday": CourtWorkingHour.Weekday.MONDAY,
            "opens_at": "10:00:00",
            "closes_at": "22:00:00",
            "is_closed": False,
        }
        data.update(extra_fields)
        return self.client.post(
            self.working_hour_list_url(club),
            data,
            format="json",
        )

    def weekly_payload(self, *rows):
        return {"working_hours": list(rows)}

    def open_row(self, weekday=CourtWorkingHour.Weekday.MONDAY, **extra_fields):
        data = {
            "weekday": weekday,
            "opens_at": "10:00:00",
            "closes_at": "22:00:00",
            "is_closed": False,
        }
        data.update(extra_fields)
        return data

    def closed_row(self, weekday=CourtWorkingHour.Weekday.TUESDAY, **extra_fields):
        data = {
            "weekday": weekday,
            "opens_at": None,
            "closes_at": None,
            "is_closed": True,
        }
        data.update(extra_fields)
        return data

    def test_owner_can_create_working_hours_for_owned_court(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_manager_can_manage_working_hours_for_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_hours(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_duplicate_same_court_weekday(self):
        CourtWorkingHour.objects.create(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
            opens_at="10:00",
            closes_at="22:00",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_opens_at_must_be_before_closes_at(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(
            self.club,
            self.court,
            opens_at="22:00:00",
            closes_at="10:00:00",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_open_day_requires_open_and_close_times(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(
            self.club,
            self.court,
            opens_at=None,
            closes_at=None,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_cannot_manage_unrelated_court_working_hours(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_hours(self.club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_list_but_not_manage_working_hours_for_assigned_court(self):
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        working_hour = CourtWorkingHour.objects.create(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.TUESDAY,
            opens_at="10:00",
            closes_at="22:00",
        )
        self.client.force_authenticate(user=self.staff)

        list_response = self.client.get(self.working_hour_list_url(self.club))
        create_response = self.post_hours(
            self.club,
            self.court,
            weekday=CourtWorkingHour.Weekday.WEDNESDAY,
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(list_response), {working_hour.id})
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_nested_get_returns_selected_court_weekly_schedule(self):
        CourtWorkingHour.objects.create(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
            opens_at="10:00",
            closes_at="22:00",
        )
        CourtWorkingHour.objects.create(
            court=self.other_court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
            opens_at="09:00",
            closes_at="21:00",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.nested_working_hour_url(self.club, self.court))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["court"], self.court.id)
        self.assertEqual(response.data["court_name"], self.court.name)
        self.assertEqual(len(response.data["working_hours"]), 7)
        monday = next(
            item
            for item in response.data["working_hours"]
            if item["weekday"] == CourtWorkingHour.Weekday.MONDAY
        )
        self.assertEqual(monday["opens_at"], "10:00:00")

    def test_nested_get_rejects_court_from_another_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.nested_working_hour_url(self.club, self.other_court)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_can_read_assigned_nested_working_hours_only(self):
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.staff)

        assigned_response = self.client.get(
            self.nested_working_hour_url(self.club, self.court)
        )
        other_response = self.client.get(
            self.nested_working_hour_url(self.club, self.other_court)
        )

        self.assertEqual(assigned_response.status_code, status.HTTP_200_OK)
        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_nested_put_updates_full_weekly_schedule(self):
        CourtWorkingHour.objects.create(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
            opens_at="08:00",
            closes_at="12:00",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(
                self.open_row(CourtWorkingHour.Weekday.MONDAY),
                self.closed_row(CourtWorkingHour.Weekday.TUESDAY),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CourtWorkingHour.objects.filter(court=self.court).count(), 7)
        monday = CourtWorkingHour.objects.get(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.MONDAY,
        )
        tuesday = CourtWorkingHour.objects.get(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.TUESDAY,
        )
        self.assertEqual(monday.opens_at.isoformat(), "10:00:00")
        self.assertTrue(tuesday.is_closed)
        self.assertIsNone(tuesday.opens_at)
        self.assertIsNone(tuesday.closes_at)

    def test_nested_put_rejects_duplicate_weekday(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(
                self.open_row(CourtWorkingHour.Weekday.MONDAY),
                self.open_row(CourtWorkingHour.Weekday.MONDAY),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_put_rejects_invalid_weekday(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(self.open_row(weekday=99)),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_put_rejects_open_day_without_times(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(
                self.open_row(opens_at=None, closes_at=None),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_put_rejects_opens_at_after_closes_at(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(
                self.open_row(opens_at="22:00:00", closes_at="10:00:00"),
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nested_put_accepts_closed_day_with_null_times(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(self.closed_row(CourtWorkingHour.Weekday.FRIDAY)),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        friday = CourtWorkingHour.objects.get(
            court=self.court,
            weekday=CourtWorkingHour.Weekday.FRIDAY,
        )
        self.assertTrue(friday.is_closed)

    def test_nested_put_rejects_court_from_another_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.other_court),
            self.weekly_payload(self.open_row()),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthorized_user_cannot_manage_nested_working_hours(self):
        self.client.force_authenticate(user=self.other_owner)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(self.open_row()),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_manage_nested_working_hours(self):
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            self.weekly_payload(self.open_row()),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_and_manager_can_manage_nested_working_hours(self):
        for actor in (self.owner, self.manager):
            with self.subTest(actor=actor.username):
                CourtWorkingHour.objects.filter(court=self.court).delete()
                self.client.force_authenticate(user=actor)

                response = self.client.put(
                    self.nested_working_hour_url(self.club, self.court),
                    self.weekly_payload(self.open_row()),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
