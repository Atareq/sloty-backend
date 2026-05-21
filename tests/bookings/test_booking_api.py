from datetime import time
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court, CourtWorkingHour
from apps.transactions.models import Transaction


class BookingAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="booking-admin") -> User:
        return self.create_user(username=username, is_platform_admin=True)

    def create_club(self, name: str, slug: str | None = None, **extra_fields) -> Club:
        data = {
            "name": name,
            "city": "Assiut",
            "area": "Downtown",
        }
        if slug is not None:
            data["slug"] = slug
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": Decimal("300.00"),
            "slot_duration_minutes": 60,
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

    def create_booking(self, court: Court, **extra_fields) -> Booking:
        start_time = extra_fields.pop("start_time", self.time_at(20))
        end_time = extra_fields.pop("end_time", self.time_at(21))
        data = {
            "club": court.club,
            "court": court,
            "customer_name": "Existing Customer",
            "customer_phone": "+201000000001",
            "start_time": start_time,
            "end_time": end_time,
            "total_price": Decimal("300.00"),
            "status": Booking.Status.HOLD,
            "source": Booking.Source.MANUAL,
        }
        data.update(extra_fields)
        return Booking.objects.create(**data)

    def create_transaction(self, booking: Booking, **extra_fields) -> Transaction:
        data = {
            "booking": booking,
            "amount": Decimal("50.00"),
            "payment_method": Transaction.PaymentMethod.CASH,
        }
        data.update(extra_fields)
        return Transaction.objects.create(**data)

    def time_at(self, hour: int, minute: int = 0):
        return timezone.datetime(
            2026,
            5,
            20,
            hour,
            minute,
            tzinfo=timezone.get_current_timezone(),
        )

    def booking_payload(self, court: Court, **extra_fields):
        data = {
            "court": court.id,
            "customer_name": "Ahmed Hassan",
            "customer_phone": "+201000000002",
            "start_time": self.time_at(20).isoformat(),
            "end_time": self.time_at(21).isoformat(),
        }
        data.update(extra_fields)
        return data

    def booking_list_url(self, club):
        return reverse("club-booking-list", kwargs={"club_slug": club.slug})

    def booking_detail_url(self, club, booking):
        return reverse(
            "club-booking-detail",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def post_booking(self, club: Club, court: Court, **extra_fields):
        return self.client.post(
            self.booking_list_url(club),
            self.booking_payload(court, **extra_fields),
            format="json",
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}


class BookingCreationTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.club = self.create_club("Booking Club", slug="booking-club")
        self.other_club = self.create_club("Other Booking Club", slug="other-booking")
        self.court = self.create_court(self.club, "Booking Court")
        self.other_court = self.create_court(self.other_club, "Other Booking Court")

    def test_club_scoped_bookings_route_works(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_booking_can_be_created_with_required_fields(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.customer_name, "Ahmed Hassan")
        self.assertEqual(str(booking.customer_phone), "+201000000002")

    def test_booking_defaults_to_hold_and_manual_source(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.status, Booking.Status.HOLD)
        self.assertEqual(booking.source, Booking.Source.MANUAL)

    def test_total_price_is_calculated_from_court_price_and_duration(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(22).isoformat(),
            total_price="1.00",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.total_price, Decimal("600.00"))
        self.assertEqual(response.data["total_price"], "600.00")

    def test_booking_club_is_set_from_url_slug_club(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get(id=response.data["id"])
        self.assertEqual(booking.club, self.club)
        self.assertEqual(response.data["club"], self.club.id)

    def test_court_from_another_club_is_rejected(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_time_must_be_before_end_time(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(21).isoformat(),
            end_time=self.time_at(20).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duration_must_match_slot_duration_multiple(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(20, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_booking_outside_working_hours_is_allowed(self):
        CourtWorkingHour.objects.create(
            court=self.court,
            weekday=2,
            opens_at=time(10, 0),
            closes_at=time(18, 0),
        )
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(22).isoformat(),
            end_time=self.time_at(23).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class BookingScopeTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("scope-admin")
        self.owner = self.create_user("scope-owner")
        self.manager = self.create_user("scope-manager")
        self.staff = self.create_user("scope-staff")
        self.club = self.create_club("Scoped Club", slug="scoped-club")
        self.other_club = self.create_club("Other Scoped Club", slug="other-scoped")
        self.court = self.create_court(self.club, "Scoped Court")
        self.same_club_other_court = self.create_court(self.club, "Scoped Other Court")
        self.other_court = self.create_court(self.other_club, "Other Scoped Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(
            self.same_club_other_court,
            customer_phone="+201000000007",
        )
        self.other_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000000003",
        )
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_anonymous_cannot_access_bookings(self):
        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_lists_selected_club_bookings_only(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_owner_sees_bookings_in_selected_owned_club_only(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )

    def test_owner_cannot_access_unrelated_club_bookings(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(self.booking_list_url(self.other_club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_sees_bookings_in_assigned_club_only(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.same_club_other_booking.id},
        )

    def test_staff_sees_bookings_for_assigned_court_only(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.booking.id})
        self.assertNotIn(self.same_club_other_booking.id, self.list_ids(response))

    def test_owner_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.booking_detail_url(self.club, self.other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(
            self.booking_detail_url(self.club, self.other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_cannot_retrieve_unrelated_booking(self):
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(
            self.booking_detail_url(self.club, self.same_club_other_booking)
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class BookingCreationPermissionTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("create-admin")
        self.owner = self.create_user("create-owner")
        self.manager = self.create_user("create-manager")
        self.staff = self.create_user("create-staff")
        self.club = self.create_club("Create Club", slug="create-club")
        self.other_club = self.create_club("Other Create Club", slug="other-create")
        self.court = self.create_court(self.club, "Create Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Same Club Other Court",
        )
        self.other_court = self.create_court(self.other_club, "Other Create Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )

    def test_platform_admin_can_create_booking_on_any_active_court_in_selected_club(
        self,
    ):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.same_club_other_court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_can_create_booking_inside_owned_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_cannot_create_booking_inside_unrelated_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_create_booking_inside_assigned_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_manager_cannot_create_booking_inside_unrelated_club(self):
        self.client.force_authenticate(user=self.manager)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_create_booking_on_assigned_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_cannot_create_booking_on_another_court_in_same_club(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.club, self.same_club_other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_create_booking_on_unrelated_club_court(self):
        self.client.force_authenticate(user=self.staff)

        response = self.post_booking(self.other_club, self.other_court)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BookingSourceAndActiveTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("source-admin")
        self.owner = self.create_user("source-owner")
        self.club = self.create_club("Source Club", slug="source-club")
        self.court = self.create_court(self.club, "Source Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)

    def test_non_platform_users_cannot_create_admin_correction_booking(self):
        self.client.force_authenticate(user=self.owner)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_platform_admin_can_create_admin_correction_booking(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            source=Booking.Source.ADMIN_CORRECTION,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], Booking.Source.ADMIN_CORRECTION)

    def test_normal_booking_source_defaults_to_manual(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], Booking.Source.MANUAL)

    def test_cannot_create_booking_on_inactive_court(self):
        self.court.is_active = False
        self.court.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_booking_when_club_is_inactive(self):
        self.club.is_active = False
        self.club.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(self.club, self.court)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BookingOverlapTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("overlap-admin")
        self.club = self.create_club("Overlap Club", slug="overlap-club")
        self.court = self.create_court(self.club, "Overlap Court")
        self.other_court = self.create_court(self.club, "Other Overlap Court")
        self.client.force_authenticate(user=self.platform_admin)

    def create_existing_booking(self, status_value):
        return self.create_booking(
            self.court,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=status_value,
        )

    def assert_overlap_is_rejected_for_status(self, status_value):
        self.create_existing_booking(status_value)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20, 30).isoformat(),
            end_time=self.time_at(21, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def assert_overlap_is_allowed_for_status(self, status_value):
        self.create_existing_booking(status_value)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20, 30).isoformat(),
            end_time=self.time_at(21, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_create_overlapping_hold_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.HOLD)

    def test_cannot_create_overlapping_confirmed_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.CONFIRMED)

    def test_can_create_adjacent_booking_ending_exactly_at_existing_start(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(19).isoformat(),
            end_time=self.time_at(20).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_can_create_adjacent_booking_starting_exactly_at_existing_end(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(21).isoformat(),
            end_time=self.time_at(22).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_can_create_overlapping_booking_on_different_court(self):
        self.create_existing_booking(Booking.Status.HOLD)

        response = self.post_booking(
            self.club,
            self.other_court,
            start_time=self.time_at(20, 30).isoformat(),
            end_time=self.time_at(21, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cancelled_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.CANCELLED)

    def test_expired_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.EXPIRED)

    def test_completed_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.COMPLETED)

    def test_no_show_booking_does_not_block_slot(self):
        self.assert_overlap_is_allowed_for_status(Booking.Status.NO_SHOW)


class BookingUpdateTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("update-admin")
        self.club = self.create_club("Update Club", slug="update-club")
        self.court = self.create_court(self.club, "Update Court")
        self.other_court = self.create_court(self.club, "Other Update Court")
        self.booking = self.create_booking(self.court)
        self.client.force_authenticate(user=self.platform_admin)

    def test_allowed_user_can_patch_basic_details(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {
                "customer_name": "Updated Customer",
                "customer_phone": "+201000000004",
                "notes": "Updated note",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.customer_name, "Updated Customer")
        self.assertEqual(str(self.booking.customer_phone), "+201000000004")
        self.assertEqual(self.booking.notes, "Updated note")

    def test_cannot_patch_status_in_sprint_3(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"status": Booking.Status.CANCELLED},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HOLD)

    def test_cannot_patch_total_price(self):
        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"total_price": "1.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.total_price, Decimal("300.00"))

    def test_cannot_patch_court_or_times_in_sprint_3(self):
        original_start = self.booking.start_time
        original_end = self.booking.end_time

        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {
                "court": self.other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.court, self.court)
        self.assertEqual(self.booking.start_time, original_start)
        self.assertEqual(self.booking.end_time, original_end)

    def test_delete_booking_is_not_allowed(self):
        response = self.client.delete(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_locked_booking_status_cannot_be_patched(self):
        self.booking.status = Booking.Status.COMPLETED
        self.booking.save(update_fields=["status"])

        response = self.client.patch(
            self.booking_detail_url(self.club, self.booking),
            {"customer_name": "Should Not Change"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class BookingFilterTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("filter-admin")
        self.owner = self.create_user("filter-owner")
        self.club = self.create_club("Filter Club", slug="filter-club")
        self.other_club = self.create_club("Other Filter Club", slug="other-filter")
        self.court = self.create_court(self.club, "Filter Court")
        self.other_court = self.create_court(self.other_club, "Other Filter Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.booking = self.create_booking(
            self.court,
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=Booking.Status.HOLD,
            source=Booking.Source.MANUAL,
        )
        self.confirmed_booking = self.create_booking(
            self.court,
            customer_phone="+201000000005",
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            status=Booking.Status.CONFIRMED,
            source=Booking.Source.ADMIN_CORRECTION,
        )
        self.other_booking = self.create_booking(
            self.other_court,
            customer_phone="+201000000006",
            start_time=self.time_at(20),
            end_time=self.time_at(21),
            status=Booking.Status.HOLD,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_filter_by_court_inside_selected_club(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"court": self.court.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )

    def test_filter_by_status(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"status": Booking.Status.CONFIRMED},
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_filter_by_source(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"source": Booking.Source.ADMIN_CORRECTION},
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_filter_by_date(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_filter_by_date_from_and_date_to(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {
                "date_from": self.time_at(21, 30).isoformat(),
                "date_to": self.time_at(22, 30).isoformat(),
            },
        )

        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_filters_still_respect_user_scope_inside_selected_club(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))


class BookingPaymentSummaryTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("payment-summary-admin")
        self.staff = self.create_user("payment-summary-staff")
        self.club = self.create_club("Payment Summary Club", slug="payment-summary")
        self.other_club = self.create_club(
            "Other Payment Summary Club",
            slug="other-payment-summary",
        )
        self.court = self.create_court(self.club, "Summary Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Summary Other Court",
        )
        self.other_court = self.create_court(self.other_club, "External Summary Court")
        self.booking = self.create_booking(self.court)
        self.same_club_other_booking = self.create_booking(self.same_club_other_court)
        self.other_booking = self.create_booking(self.other_court)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=self.platform_admin)

    def test_booking_list_includes_zero_payment_summary_without_transactions(self):
        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking_data = next(
            item for item in response.data["results"] if item["id"] == self.booking.id
        )
        self.assertEqual(booking_data["paid_amount"], "0.00")
        self.assertEqual(booking_data["remaining_amount"], "300.00")
        self.assertFalse(booking_data["is_fully_paid"])

    def test_booking_detail_includes_payment_summary(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["paid_amount"], "100.00")
        self.assertEqual(response.data["remaining_amount"], "200.00")
        self.assertFalse(response.data["is_fully_paid"])

    def test_payment_summary_is_correct_after_partial_payment(self):
        self.create_transaction(self.booking, amount=Decimal("125.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.data["paid_amount"], "125.00")
        self.assertEqual(response.data["remaining_amount"], "175.00")
        self.assertFalse(response.data["is_fully_paid"])

    def test_payment_summary_is_fully_paid_after_full_payment(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.booking, amount=Decimal("200.00"))

        response = self.client.get(self.booking_detail_url(self.club, self.booking))

        self.assertEqual(response.data["paid_amount"], "300.00")
        self.assertEqual(response.data["remaining_amount"], "0.00")
        self.assertTrue(response.data["is_fully_paid"])

    def test_payment_summary_respects_club_scoped_booking_access(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.other_booking, amount=Decimal("200.00"))

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.booking.id, self.list_ids(response))
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_staff_sees_payment_summary_only_for_assigned_court_bookings(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(self.same_club_other_booking, amount=Decimal("200.00"))
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.booking_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.booking.id})
        booking_data = response.data["results"][0]
        self.assertEqual(booking_data["paid_amount"], "100.00")
        self.assertEqual(booking_data["remaining_amount"], "200.00")
        self.assertFalse(booking_data["is_fully_paid"])
