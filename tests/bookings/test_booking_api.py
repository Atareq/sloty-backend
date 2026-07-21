from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.urls import resolve, reverse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.bookings.filters import BookingFilter
from apps.bookings.models import Booking
from apps.bookings.views import BookingViewSet
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

    def booking_lifecycle_url(self, club, booking, action_name):
        return reverse(
            f"club-booking-{action_name}",
            kwargs={"club_slug": club.slug, "pk": booking.pk},
        )

    def booking_slots_url(self, club):
        return reverse("club-booking-slots", kwargs={"club_slug": club.slug})

    def post_booking(self, club: Club, court: Court, **extra_fields):
        return self.client.post(
            self.booking_list_url(club),
            self.booking_payload(court, **extra_fields),
            format="json",
        )

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def assert_field_error(self, response, field):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn(field, response.data["field_errors"])

    def assert_api_error(self, response, code):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], code)
        self.assertIn("message", response.data)

    def create_working_hours(
        self,
        court: Court,
        *,
        weekday=2,
        opens_at=time(9, 0),
        closes_at=time(12, 0),
        is_closed=False,
    ) -> CourtWorkingHour:
        return CourtWorkingHour.objects.create(
            court=court,
            weekday=weekday,
            opens_at=opens_at if not is_closed else None,
            closes_at=closes_at if not is_closed else None,
            is_closed=is_closed,
        )


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
        self.assert_field_error(response, "court")

    def test_start_time_must_be_before_end_time(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(21).isoformat(),
            end_time=self.time_at(20).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "end_time")

    def test_duration_must_match_slot_duration_multiple(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.post_booking(
            self.club,
            self.court,
            start_time=self.time_at(20).isoformat(),
            end_time=self.time_at(20, 30).isoformat(),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "end_time")

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


class BookingSlotAvailabilityTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.club = self.create_club("Slots Club", slug="slots-club")
        self.court = self.create_court(self.club, "Slots Court")
        self.create_working_hours(self.court)
        self.client.force_authenticate(user=self.platform_admin)

    def get_slots(self, **params):
        data = {"court": self.court.id, "date": "2026-05-20"}
        data.update(params)
        return self.client.get(self.booking_slots_url(self.club), data)

    def slot_by_hour(self, response, hour):
        start_time_prefix = self.time_at(hour).strftime("%Y-%m-%dT%H:%M:%S")
        expected_datetime = self.time_at(hour)
        return next(
            slot
            for slot in response.data["slots"]
            if (
                slot["start_time"] == expected_datetime
                or (
                    isinstance(slot["start_time"], str)
                    and slot["start_time"].startswith(start_time_prefix)
                )
            )
        )

    def test_slots_endpoint_returns_free_slots_when_no_booking_exists(self):
        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["court"], self.court.id)
        self.assertEqual(response.data["slot_duration_minutes"], 60)
        self.assertEqual(len(response.data["slots"]), 3)
        first_slot = response.data["slots"][0]
        self.assertEqual(first_slot["slot_status"], "FREE")
        self.assertEqual(first_slot["is_available"], True)
        self.assertIsNone(first_slot["booking"])
        self.assertEqual(first_slot["label"], "Available")

    def test_slots_endpoint_marks_blocking_booking_statuses(self):
        hold = self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.HOLD,
        )
        confirmed = self.create_booking(
            self.court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.CONFIRMED,
        )
        completed = self.create_booking(
            self.court,
            start_time=self.time_at(11),
            end_time=self.time_at(12),
            status=Booking.Status.COMPLETED,
        )
        self.create_transaction(completed, amount=completed.total_price)

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["slot_status"], "HOLD")
        self.assertEqual(self.slot_by_hour(response, 9)["booking"]["id"], hold.id)
        self.assertEqual(self.slot_by_hour(response, 10)["slot_status"], "CONFIRMED")
        self.assertEqual(self.slot_by_hour(response, 10)["booking"]["id"], confirmed.id)
        completed_slot = self.slot_by_hour(response, 11)
        self.assertEqual(completed_slot["slot_status"], "COMPLETED")
        self.assertEqual(completed_slot["is_available"], False)
        self.assertEqual(completed_slot["booking"]["id"], completed.id)
        self.assertEqual(completed_slot["booking"]["remaining_amount"], "0.00")

    def test_cancelled_and_expired_bookings_do_not_block_slots(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.CANCELLED,
        )
        self.create_booking(
            self.court,
            start_time=self.time_at(10),
            end_time=self.time_at(11),
            status=Booking.Status.EXPIRED,
        )

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["slot_status"], "FREE")
        self.assertEqual(self.slot_by_hour(response, 10)["slot_status"], "FREE")

    def test_free_is_not_a_booking_status_choice(self):
        status_values = {choice[0] for choice in Booking.Status.choices}

        self.assertNotIn("FREE", status_values)

    def test_closed_day_returns_empty_slots_with_localized_message(self):
        CourtWorkingHour.objects.filter(court=self.court).delete()
        self.create_working_hours(
            self.court,
            weekday=CourtWorkingHour.Weekday.WEDNESDAY,
            is_closed=True,
        )
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["slots"], [])
        self.assertEqual(response.data["message"], "الملعب مغلق في هذا اليوم.")

    def test_slot_labels_are_localized_to_arabic(self):
        self.create_booking(
            self.court,
            start_time=self.time_at(9),
            end_time=self.time_at(10),
            status=Booking.Status.HOLD,
        )
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.slot_by_hour(response, 9)["label"], "حجز مؤقت")
        self.assertEqual(self.slot_by_hour(response, 10)["label"], "متاح")

    def test_date_range_too_large_returns_slot_period_error(self):
        response = self.client.get(
            self.booking_slots_url(self.club),
            {
                "court": self.court.id,
                "date_from": "2026-05-01",
                "date_to": "2026-06-15",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_api_error(response, "SLOT_PERIOD_TOO_LARGE")

    def test_slots_query_count_does_not_grow_with_generated_slots(self):
        CourtWorkingHour.objects.filter(court=self.court).delete()
        self.create_working_hours(
            self.court,
            opens_at=time(0, 0),
            closes_at=time(23, 0),
        )

        with self.assertNumQueries(4):
            response = self.get_slots()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["slots"]), 23)


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
        self.assert_field_error(response, "source")

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
        self.assert_field_error(response, "court")

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

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

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

    def test_cannot_create_overlapping_completed_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.COMPLETED)

    def test_cannot_create_overlapping_no_show_booking_on_same_court(self):
        self.assert_overlap_is_rejected_for_status(Booking.Status.NO_SHOW)


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


class BookingLifecycleActionTests(BookingAPITestCase):
    detail_response_fields = {
        "id",
        "club",
        "court",
        "customer_name",
        "customer_phone",
        "start_time",
        "end_time",
        "total_price",
        "paid_amount",
        "remaining_amount",
        "is_fully_paid",
        "status",
        "source",
        "notes",
        "cancellation_reason",
        "no_show_reason",
        "reschedule_reason",
        "completed_at",
        "cancelled_at",
        "no_show_at",
        "expired_at",
        "created_by",
        "created",
        "modified",
    }

    def setUp(self):
        self.platform_admin = self.create_platform_admin("lifecycle-admin")
        self.owner = self.create_user("lifecycle-owner")
        self.manager = self.create_user("lifecycle-manager")
        self.staff = self.create_user("lifecycle-staff")
        self.other_user = self.create_user("lifecycle-other-user")
        self.club = self.create_club("Lifecycle Club", slug="lifecycle-club")
        self.other_club = self.create_club(
            "Other Lifecycle Club",
            slug="other-lifecycle",
        )
        self.court = self.create_court(self.club, "Lifecycle Court")
        self.same_club_other_court = self.create_court(
            self.club,
            "Lifecycle Other Court",
        )
        self.other_court = self.create_court(self.other_club, "Other Lifecycle Court")
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.create_membership(
            self.other_user,
            self.other_club,
            ClubMembership.Role.OWNER,
        )

    def post_lifecycle(self, club, booking, action_name, user, payload=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        return self.client.post(
            self.booking_lifecycle_url(club, booking, action_name),
            payload or {},
            format="json",
        )

    def test_anonymous_cannot_call_lifecycle_actions(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(self.club, booking, "cancel", None)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_platform_admin_can_cancel_hold(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertIsNotNone(booking.cancelled_at)

    def test_owner_can_cancel_confirmed(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "cancel",
            self.owner,
            {"reason": "Customer cancelled"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.cancellation_reason, "Customer cancelled")

    def test_manager_can_complete_confirmed(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.manager,
            {"confirm_collect_remaining_cash": True},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertIsNotNone(booking.completed_at)

    def test_staff_can_change_booking_status_for_assigned_court(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "no-show",
            self.staff,
            {"reason": "Customer did not arrive"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.NO_SHOW)
        self.assertEqual(booking.no_show_reason, "Customer did not arrive")
        self.assertIsNotNone(booking.no_show_at)

    def test_staff_cannot_change_booking_status_for_another_court_in_same_club(self):
        booking = self.create_booking(
            self.same_club_other_court,
            status=Booking.Status.CONFIRMED,
        )

        response = self.post_lifecycle(self.club, booking, "complete", self.staff)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_unrelated_club_member_cannot_access_selected_club_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(self.club, booking, "complete", self.other_user)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_allowed_transitions_succeed(self):
        cases = (
            (Booking.Status.HOLD, "cancel", Booking.Status.CANCELLED, 10),
            (Booking.Status.HOLD, "expire", Booking.Status.EXPIRED, 11),
            (Booking.Status.CONFIRMED, "cancel", Booking.Status.CANCELLED, 12),
            (Booking.Status.CONFIRMED, "complete", Booking.Status.COMPLETED, 13),
            (Booking.Status.CONFIRMED, "no-show", Booking.Status.NO_SHOW, 14),
        )
        for source_status, action_name, target_status, phone_suffix in cases:
            with self.subTest(source_status=source_status, action_name=action_name):
                booking = self.create_booking(
                    self.court,
                    status=source_status,
                    start_time=self.time_at(phone_suffix),
                    end_time=self.time_at(phone_suffix + 1),
                    customer_phone=f"+2010000003{phone_suffix:02d}",
                )
                if action_name == "complete":
                    self.create_transaction(booking, amount=booking.total_price)

                payload = (
                    {"confirm_collect_remaining_cash": True}
                    if action_name == "complete"
                    else {}
                )
                response = self.post_lifecycle(
                    self.club,
                    booking,
                    action_name,
                    self.platform_admin,
                    payload,
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                booking.refresh_from_db()
                self.assertEqual(booking.status, target_status)

    def test_hold_cannot_be_completed_or_marked_no_show(self):
        cases = (
            ("complete", Booking.Status.COMPLETED, 15),
            ("no-show", Booking.Status.NO_SHOW, 16),
        )
        for action_name, target_status, phone_suffix in cases:
            with self.subTest(action_name=action_name):
                booking = self.create_booking(
                    self.court,
                    status=Booking.Status.HOLD,
                    start_time=self.time_at(phone_suffix),
                    end_time=self.time_at(phone_suffix + 1),
                    customer_phone=f"+2010000004{phone_suffix:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    action_name,
                    self.platform_admin,
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assert_api_error(
                    response,
                    "INVALID_BOOKING_STATUS_TRANSITION",
                )
                booking.refresh_from_db()
                self.assertEqual(booking.status, Booking.Status.HOLD)
                self.assertNotIn("booking", response.data)
                self.assertNotEqual(booking.status, target_status)

    def test_terminal_statuses_cannot_transition(self):
        terminal_statuses = (
            Booking.Status.COMPLETED,
            Booking.Status.CANCELLED,
            Booking.Status.NO_SHOW,
            Booking.Status.EXPIRED,
        )
        for index, terminal_status in enumerate(terminal_statuses, start=17):
            with self.subTest(terminal_status=terminal_status):
                booking = self.create_booking(
                    self.court,
                    status=terminal_status,
                    start_time=self.time_at(index),
                    end_time=self.time_at(index + 1),
                    customer_phone=f"+2010000005{index:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    "cancel",
                    self.platform_admin,
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                expected_code = (
                    "BOOKING_ALREADY_CANCELLED"
                    if terminal_status == Booking.Status.CANCELLED
                    else "INVALID_BOOKING_STATUS_TRANSITION"
                )
                self.assert_api_error(response, expected_code)
                self.assertNotIn("booking", response.data)
                booking.refresh_from_db()
                self.assertEqual(booking.status, terminal_status)

    def test_successful_action_returns_booking_detail_shape(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), self.detail_response_fields)

    def test_staff_cancel_requires_reason(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(self.club, booking, "cancel", self.staff)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_field_error(response, "reason")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_no_show_requires_confirmed_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "no-show",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "INVALID_BOOKING_STATUS_TRANSITION")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.HOLD)

    def test_expire_requires_hold_booking(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "expire",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "INVALID_BOOKING_STATUS_TRANSITION")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_complete_fully_paid_booking_succeeds_without_auto_cash(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=booking.total_price)

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.COMPLETED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_complete_with_remaining_amount_returns_domain_conflict(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        self.assertEqual(
            response.data["message"],
            "This booking cannot be completed until the remaining amount is paid.",
        )
        self.assertEqual(
            response.data["details"],
            {
                "booking_id": booking.id,
                "remaining_amount": "200.00",
            },
        )
        self.assertNotIn("booking", response.data)
        self.assertNotIn("detail", response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_complete_with_remaining_amount_returns_localized_arabic_error(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))
        self.client.credentials(HTTP_ACCEPT_LANGUAGE="ar")

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        self.assertEqual(
            response.data["message"],
            "لا يمكن إكمال الحجز قبل سداد المبلغ المتبقي.",
        )

    def test_completion_remaining_amount_ignores_cancelled_transactions(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))
        self.create_transaction(
            booking,
            amount=Decimal("200.00"),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Wrong completion payment",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_complete_with_confirmation_still_rejects_remaining_amount(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        self.create_transaction(booking, amount=Decimal("100.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "complete",
            self.platform_admin,
            {"confirm_collect_remaining_cash": True},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.transactions.count(), 1)

    def test_reschedule_hold_booking_to_free_slot(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.same_club_other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
                "reason": "Customer changed time",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.court, self.same_club_other_court)
        self.assertEqual(booking.start_time, self.time_at(22))
        self.assertEqual(booking.reschedule_reason, "Customer changed time")
        self.assertEqual(booking.transactions.count(), 0)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_RESCHEDULED,
                entity_type="Booking",
                entity_id=booking.id,
            ).exists()
        )

    def test_reschedule_confirmed_booking_keeps_transactions_attached(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)
        transaction_obj = self.create_transaction(booking, amount=Decimal("50.00"))

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transaction_obj.refresh_from_db()
        self.assertEqual(transaction_obj.booking_id, booking.id)

    def test_reschedule_blocks_overlapping_active_booking(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            customer_phone="+201000000777",
        )
        self.create_booking(
            self.court,
            status=Booking.Status.HOLD,
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            customer_phone="+201000000778",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

    def test_reschedule_blocks_overlapping_completed_booking(self):
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            start_time=self.time_at(18),
            end_time=self.time_at(19),
            customer_phone="+201000000779",
        )
        self.create_booking(
            self.court,
            status=Booking.Status.COMPLETED,
            start_time=self.time_at(22),
            end_time=self.time_at(23),
            customer_phone="+201000000780",
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assert_api_error(response, "BOOKING_SLOT_UNAVAILABLE")
        self.assertNotIn("booking", response.data)

    def test_reschedule_excludes_current_booking_from_overlap_check(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": self.court.id,
                "start_time": self.time_at(20).isoformat(),
                "end_time": self.time_at(21).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_cannot_reschedule_outside_assigned_court(self):
        booking = self.create_booking(self.court, status=Booking.Status.CONFIRMED)

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.staff,
            {
                "court": self.same_club_other_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reschedule_higher_price_updates_total_price(self):
        expensive_court = self.create_court(
            self.club,
            "Expensive Court",
            default_price=Decimal("450.00"),
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": expensive_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.total_price, Decimal("450.00"))

    def test_reschedule_lower_price_keeps_existing_total_price(self):
        cheaper_court = self.create_court(
            self.club,
            "Cheaper Court",
            default_price=Decimal("200.00"),
        )
        booking = self.create_booking(
            self.court,
            status=Booking.Status.CONFIRMED,
            total_price=Decimal("300.00"),
        )

        response = self.post_lifecycle(
            self.club,
            booking,
            "reschedule",
            self.platform_admin,
            {
                "court": cheaper_court.id,
                "start_time": self.time_at(22).isoformat(),
                "end_time": self.time_at(23).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.total_price, Decimal("300.00"))

    def test_terminal_statuses_cannot_be_rescheduled(self):
        for index, terminal_status in enumerate(Booking.LOCKED_STATUSES, start=1):
            with self.subTest(terminal_status=terminal_status):
                booking = self.create_booking(
                    self.court,
                    status=terminal_status,
                    start_time=self.time_at(index),
                    end_time=self.time_at(index + 1),
                    customer_phone=f"+2010000009{index:02d}",
                )

                response = self.post_lifecycle(
                    self.club,
                    booking,
                    "reschedule",
                    self.platform_admin,
                    {
                        "court": self.court.id,
                        "start_time": self.time_at(22).isoformat(),
                        "end_time": self.time_at(23).isoformat(),
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assert_api_error(
                    response,
                    "INVALID_BOOKING_STATUS_TRANSITION",
                )

    def test_expire_sets_timestamp_and_audit_log(self):
        booking = self.create_booking(self.court, status=Booking.Status.HOLD)

        response = self.post_lifecycle(
            self.club,
            booking,
            "expire",
            self.platform_admin,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.EXPIRED)
        self.assertIsNotNone(booking.expired_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.BOOKING_EXPIRED,
                entity_type="Booking",
                entity_id=booking.id,
            ).exists()
        )


class BookingAutomaticExpiryCommandTests(BookingAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("expiry-admin")
        self.club = self.create_club("Expiry Club", slug="expiry-club")
        self.court = self.create_court(
            self.club,
            "Expiry Court",
            internal_hold_expiry_hours=12,
        )

    def age_booking(self, booking, *, hours):
        created = timezone.now() - timedelta(hours=hours)
        Booking.objects.filter(pk=booking.pk).update(created=created)
        booking.refresh_from_db()
        return booking

    def test_expire_hold_bookings_expires_due_holds_only_and_is_idempotent(self):
        due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=self.time_at(8),
                end_time=self.time_at(9),
                customer_phone="+201000001001",
            ),
            hours=13,
        )
        not_due_hold = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.HOLD,
                start_time=self.time_at(9),
                end_time=self.time_at(10),
                customer_phone="+201000001002",
            ),
            hours=2,
        )
        confirmed = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.CONFIRMED,
                start_time=self.time_at(10),
                end_time=self.time_at(11),
                customer_phone="+201000001003",
            ),
            hours=13,
        )
        terminal = self.age_booking(
            self.create_booking(
                self.court,
                status=Booking.Status.CANCELLED,
                start_time=self.time_at(11),
                end_time=self.time_at(12),
                customer_phone="+201000001004",
            ),
            hours=13,
        )

        call_command("expire_hold_bookings", verbosity=0)
        call_command("expire_hold_bookings", verbosity=0)

        due_hold.refresh_from_db()
        not_due_hold.refresh_from_db()
        confirmed.refresh_from_db()
        terminal.refresh_from_db()
        self.assertEqual(due_hold.status, Booking.Status.EXPIRED)
        self.assertIsNotNone(due_hold.expired_at)
        self.assertEqual(not_due_hold.status, Booking.Status.HOLD)
        self.assertEqual(confirmed.status, Booking.Status.CONFIRMED)
        self.assertEqual(terminal.status, Booking.Status.CANCELLED)
        audit_logs = AuditLog.objects.filter(
            action=AuditLog.Action.BOOKING_EXPIRED,
            entity_type="Booking",
            entity_id=due_hold.id,
        )
        self.assertEqual(audit_logs.count(), 1)
        self.assertIsNone(audit_logs.get().actor)
        self.assertEqual(
            audit_logs.get().metadata,
            {"source": "automatic_hold_expiry"},
        )


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

    def test_invalid_date_filter_returns_400(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "not-a-date"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_datetime_filter_returns_400(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"date_from": "not-a-datetime"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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

    def test_filters_still_respect_staff_assigned_court_scope(self):
        staff = self.create_user("filter-staff")
        same_club_other_court = self.create_court(
            self.club,
            "Hidden Staff Filter Court",
        )
        same_club_other_booking = self.create_booking(
            same_club_other_court,
            customer_phone="+201000000007",
            start_time=self.time_at(20),
            end_time=self.time_at(21),
        )
        self.create_membership(
            staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
        )
        self.client.force_authenticate(user=staff)

        response = self.client.get(
            self.booking_list_url(self.club),
            {"date": "2026-05-20"},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(same_club_other_booking.id, self.list_ids(response))

    def test_club_query_param_does_not_control_club_scoped_filtering(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"club": self.other_club.id},
        )

        self.assertEqual(
            self.list_ids(response),
            {self.booking.id, self.confirmed_booking.id},
        )
        self.assertNotIn(self.other_booking.id, self.list_ids(response))

    def test_needs_action_filter_excludes_completed_bookings_with_remaining_amount(
        self,
    ):
        completed_with_remaining = self.create_booking(
            self.court,
            customer_phone="+201000000008",
            start_time=self.time_at(23),
            end_time=self.time_at(23, 30),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"needs_action": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.booking.id, self.list_ids(response))
        self.assertIn(self.confirmed_booking.id, self.list_ids(response))
        self.assertNotIn(completed_with_remaining.id, self.list_ids(response))

    def test_overdue_filter_returns_ended_bookings(self):
        response = self.client.get(
            self.booking_list_url(self.club),
            {"status": Booking.Status.CONFIRMED, "overdue": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {self.confirmed_booking.id})

    def test_remaining_amount_gt_and_ended_filter_returns_confirmed_ended_bookings(
        self,
    ):
        completed_with_remaining = self.create_booking(
            self.court,
            customer_phone="+201000000009",
            start_time=self.time_at(23),
            end_time=self.time_at(23, 30),
            status=Booking.Status.COMPLETED,
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"remaining_amount_gt": "0", "ended": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.confirmed_booking.id, self.list_ids(response))
        self.assertNotIn(completed_with_remaining.id, self.list_ids(response))

    def test_hold_expiring_filter_uses_internal_hold_expiry_hours(self):
        self.court.internal_hold_expiry_hours = 1
        self.court.save(update_fields=["internal_hold_expiry_hours"])
        expiring_hold = self.create_booking(
            self.court,
            customer_phone="+201000000010",
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=3),
            status=Booking.Status.HOLD,
        )
        Booking.objects.filter(pk=expiring_hold.pk).update(
            created=timezone.now() - timedelta(minutes=45)
        )

        response = self.client.get(
            self.booking_list_url(self.club),
            {"hold_expiring": "true"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), {expiring_hold.id})


class BookingFilterPatternTests(BookingAPITestCase):
    def test_booking_route_resolves_to_viewset(self):
        match = resolve("/api/v1/clubs/example-club/bookings/")

        self.assertIs(match.func.cls, BookingViewSet)

    def test_booking_viewset_uses_django_filter_backend(self):
        self.assertEqual(BookingViewSet.filter_backends, (DjangoFilterBackend,))
        self.assertIs(BookingViewSet.filterset_class, BookingFilter)

    def test_booking_viewset_does_not_manually_parse_filter_query_params(self):
        repo_root = Path(__file__).resolve().parents[2]
        view_source = (repo_root / "apps" / "bookings" / "views.py").read_text()

        self.assertNotIn("request.query_params", view_source)
        self.assertNotIn("parse_date", view_source)
        self.assertNotIn("parse_datetime", view_source)

    def test_booking_filter_does_not_contain_access_logic(self):
        repo_root = Path(__file__).resolve().parents[2]
        filter_source = (repo_root / "apps" / "bookings" / "filters.py").read_text()

        self.assertNotIn("ClubMembership", filter_source)
        self.assertNotIn("ClubAccessContext", filter_source)
        self.assertNotIn("get_access_context", filter_source)
        self.assertNotIn("club_slug", filter_source)


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

    def test_payment_summary_ignores_cancelled_transactions(self):
        self.create_transaction(self.booking, amount=Decimal("100.00"))
        self.create_transaction(
            self.booking,
            amount=Decimal("200.00"),
            is_cancelled=True,
            cancelled_by=self.platform_admin,
            cancelled_at=timezone.now(),
            cancellation_reason="Wrong payment",
        )

        detail_response = self.client.get(
            self.booking_detail_url(self.club, self.booking)
        )
        list_response = self.client.get(self.booking_list_url(self.club))
        list_item = next(
            item
            for item in list_response.data["results"]
            if item["id"] == self.booking.id
        )

        for data in (detail_response.data, list_item):
            self.assertEqual(data["paid_amount"], "100.00")
            self.assertEqual(data["remaining_amount"], "200.00")
            self.assertFalse(data["is_fully_paid"])

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
