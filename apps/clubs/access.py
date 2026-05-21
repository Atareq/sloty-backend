from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property
from rest_framework.exceptions import NotAuthenticated

from apps.clubs.models import Club, ClubMembership


class ClubAccessContext:
    def __init__(self, *, request, club):
        self.request = request
        self.user = request.user
        self.club = club

    @classmethod
    def from_request(cls, request, club_slug):
        if not request.user.is_authenticated:
            raise NotAuthenticated("Authentication credentials were not provided.")
        club = get_object_or_404(Club, slug=club_slug)
        return cls(request=request, club=club)

    @cached_property
    def active_memberships(self):
        return list(
            ClubMembership.objects.filter(
                club=self.club,
                user=self.user,
                is_active=True,
            )
            .select_related("club", "court", "user")
            .order_by("id")
        )

    @property
    def is_platform_admin(self):
        return self.user.is_platform_super_admin()

    @property
    def is_owner(self):
        return any(
            membership.role == ClubMembership.Role.OWNER
            for membership in self.active_memberships
        )

    @property
    def is_manager(self):
        return any(
            membership.role == ClubMembership.Role.MANAGER
            for membership in self.active_memberships
        )

    @property
    def is_staff(self):
        return any(
            membership.role == ClubMembership.Role.STAFF
            for membership in self.active_memberships
        )

    @cached_property
    def staff_court_ids(self):
        return {
            membership.court_id
            for membership in self.active_memberships
            if membership.role == ClubMembership.Role.STAFF and membership.court_id
        }

    def has_any_club_access(self):
        return self.is_platform_admin or bool(self.active_memberships)

    def can_manage_memberships(self):
        return self.is_platform_admin or self.is_owner

    def can_create_membership(self, role, court=None):
        if not self.has_any_club_access():
            return False
        if court is not None and court.club_id != self.club.id:
            return False
        if self.is_platform_admin:
            return True
        return self.is_owner and role in {
            ClubMembership.Role.MANAGER,
            ClubMembership.Role.STAFF,
        }

    def can_manage_club(self):
        return self.is_platform_admin or self.is_owner

    def can_create_court(self):
        return self.is_platform_admin or self.is_owner

    def can_update_court(self, court, attrs):
        if not self.can_access_court(court):
            return False
        if self.is_platform_admin or self.is_owner:
            return True
        if self.is_manager:
            return (
                set(attrs) == {"default_price"} and self.club.manager_can_change_pricing
            )
        return False

    def can_manage_working_hours(self, court):
        return self.can_access_court(court) and (
            self.is_platform_admin or self.is_owner or self.is_manager
        )

    def can_access_court(self, court):
        if court is None or court.club_id != self.club.id:
            return False
        if self.is_platform_admin or self.is_owner or self.is_manager:
            return True
        return court.id in self.staff_court_ids

    def can_create_booking_for_court(self, court):
        return self.can_access_court(court)

    def can_create_transaction_for_booking(self, booking):
        return (
            booking is not None
            and booking.club_id == self.club.id
            and self.can_access_court(booking.court)
        )

    def can_access_transaction(self, transaction):
        return (
            transaction is not None
            and transaction.club_id == self.club.id
            and self.can_access_court(transaction.court)
        )

    def scoped_courts_queryset(self):
        from apps.courts.models import Court

        queryset = Court.objects.filter(club=self.club)
        if self.is_platform_admin or self.is_owner or self.is_manager:
            return queryset
        if self.is_staff:
            return queryset.filter(id__in=self.staff_court_ids)
        return queryset.none()

    def scoped_working_hours_queryset(self):
        from apps.courts.models import CourtWorkingHour

        return CourtWorkingHour.objects.filter(court__in=self.scoped_courts_queryset())

    def scoped_bookings_queryset(self):
        from apps.bookings.models import Booking

        return Booking.objects.filter(court__in=self.scoped_courts_queryset())

    def scoped_transactions_queryset(self):
        from apps.transactions.models import Transaction

        return Transaction.objects.filter(court__in=self.scoped_courts_queryset())

    def scoped_memberships_queryset(self):
        queryset = ClubMembership.objects.filter(club=self.club)
        if self.can_manage_memberships():
            return queryset
        return queryset.none()
