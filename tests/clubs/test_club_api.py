from decimal import Decimal

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile
from apps.transactions.models import Transaction
from tests.booking_factories import persist_booking


class ClubAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="platform-admin") -> User:
        user = self.create_user(username=username)
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"role": Profile.Role.ADMIN}
        )
        AdminProfile.objects.get_or_create(profile=profile)
        return user

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
    ):
        if role == "OWNER":
            profile, _ = Profile.objects.get_or_create(
                user=user, defaults={"role": Profile.Role.OWNER}
            )
            owner_profile, _ = OwnerProfile.objects.get_or_create(profile=profile)
            owner_profile.clubs.add(club)
            return owner_profile
        elif role == "STAFF":
            profile, _ = Profile.objects.get_or_create(
                user=user, defaults={"role": Profile.Role.STAFF}
            )
            staff_profile, _ = StaffProfile.objects.get_or_create(
                profile=profile, defaults={"court": court}
            )
            return staff_profile
        elif role == "ADMIN":
            profile, _ = Profile.objects.get_or_create(
                user=user, defaults={"role": Profile.Role.ADMIN}
            )
            admin_profile, _ = AdminProfile.objects.get_or_create(profile=profile)
            return admin_profile
        return None

    def time_at(self, hour: int):
        return timezone.datetime(
            2026,
            7,
            2,
            hour,
            tzinfo=timezone.get_current_timezone(),
        )

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        extra_fields.setdefault("start_time", self.time_at(20))
        extra_fields.setdefault("end_time", self.time_at(21))
        extra_fields.setdefault("customer_name", "Club Customer")
        extra_fields.setdefault("customer_phone", "+201000000001")
        extra_fields.setdefault("total_price", Decimal("250.00"))
        extra_fields.setdefault("status", Booking.Status.CONFIRMED)
        return persist_booking(court, **extra_fields)

    def create_transaction(self, booking: Booking, **extra_fields) -> Transaction:
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        return Transaction.objects.create(**data)

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
        self.create_membership(self.owner, owned_club, "OWNER")
        self.create_membership(
            self.other_owner,
            unrelated_club,
            "OWNER",
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {owned_club.id})

    def test_staff_can_list_club_through_staff_membership(self):
        club = self.create_club("Staff Club")
        court = self.create_court(club, "Staff Court")
        self.create_membership(
            self.staff,
            club,
            "STAFF",
            court=court,
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(reverse("club-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {club.id})

    def test_owner_can_update_owned_club(self):
        club = self.create_club("Owned Update Club")
        self.create_membership(self.owner, club, "OWNER")
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
