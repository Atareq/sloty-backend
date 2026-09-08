from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated

from apps.clubs.models import Club, ClubMembership
from apps.common.exceptions import SlotyAPIException

CLUB_ACCESS_REVOKED_MESSAGE = _("Your access to the selected club is no longer active.")


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
        access = cls(request=request, club=club)
        if not access.has_any_club_access():
            raise SlotyAPIException(
                status_code=status.HTTP_403_FORBIDDEN,
                code="CLUB_ACCESS_REVOKED",
                message=CLUB_ACCESS_REVOKED_MESSAGE,
                details={
                    "club_slug": club.slug,
                },
            )
        return access

    @cached_property
    def active_memberships(self):
        return list(
            ClubMembership.objects.granting_access()
            .filter(
                club=self.club,
                user=self.user,
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
    def active_manager_membership(self):
        return next(
            (
                membership
                for membership in self.active_memberships
                if membership.role == ClubMembership.Role.MANAGER
            ),
            None,
        )

    @property
    def manager_can_settle_transactions(self):
        membership = self.active_manager_membership
        return bool(membership and membership.manager_can_settle_transactions)

    @property
    def manager_can_change_pricing(self):
        membership = self.active_manager_membership
        return bool(membership and membership.manager_can_change_pricing)

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

    def user_has_active_membership(self, user):
        return self.active_memberships_for_user(user).exists()

    def active_memberships_for_user(self, user):
        return ClubMembership.objects.granting_access().filter(
            club=self.club,
            user=user,
        )

    def active_roles_for_user(self, user):
        return set(
            self.active_memberships_for_user(user).values_list("role", flat=True)
        )

    def active_roles_by_user_ids(self, user_ids):
        mapping = {user_id: set() for user_id in user_ids}
        if not user_ids:
            return mapping
        rows = (
            ClubMembership.objects.granting_access()
            .filter(
                club=self.club,
                user_id__in=user_ids,
            )
            .values_list("user_id", "role")
        )
        for user_id, role in rows:
            mapping.setdefault(user_id, set()).add(role)
        return mapping

    def can_preview_settlement_for_roles(self, *, user_id, roles):
        if not self.has_any_club_access():
            return False
        if user_id == self.user.id:
            return self.is_platform_admin or bool(roles)
        if not roles:
            return False
        if self.is_platform_admin or self.is_owner:
            return True
        if self.manager_can_settle_transactions:
            return ClubMembership.Role.OWNER not in roles and bool(
                roles
                & {
                    ClubMembership.Role.MANAGER,
                    ClubMembership.Role.STAFF,
                }
            )
        return False

    def can_approve_settlement_for_roles(self, *, user_id, roles):
        if not self.can_manage_settlements():
            return False
        if user_id == self.user.id:
            return self.is_platform_admin or self.is_owner
        if not roles:
            return False
        if self.is_platform_admin or self.is_owner:
            return True
        if self.manager_can_settle_transactions:
            return ClubMembership.Role.OWNER not in roles and bool(
                roles
                & {
                    ClubMembership.Role.MANAGER,
                    ClubMembership.Role.STAFF,
                }
            )
        return False

    def can_manage_memberships(self):
        return self.is_platform_admin or self.is_owner

    def can_list_club_users(self):
        return self.is_platform_admin or self.is_owner or self.is_manager

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
        return False

    def can_manage_working_hours(self, court):
        return self.can_access_court(court) and (
            self.is_platform_admin or self.is_owner or self.manager_can_change_pricing
        )

    def can_access_court(self, court):
        if court is None or court.club_id != self.club.id:
            return False
        if self.is_platform_admin or self.is_owner or self.is_manager:
            return True
        return court.id in self.staff_court_ids

    def can_create_booking_for_court(self, court):
        return self.can_access_court(court)

    def can_change_booking_status(self, booking):
        return (
            booking is not None
            and booking.club_id == self.club.id
            and self.can_access_court(booking.court)
        )

    def can_access_booking_attempt(self, attempt):
        if not (
            attempt is not None
            and attempt.club_id == self.club.id
            and self.can_access_court(attempt.court)
        ):
            return False
        if self.is_staff_only:
            return attempt.attempted_by_id == self.user.id
        return True

    def can_dismiss_booking_attempt(self, attempt):
        return (
            self.can_access_booking_attempt(attempt)
            and attempt.attempted_by_id == self.user.id
        )

    def can_create_transaction_for_booking(self, booking):
        return (
            booking is not None
            and booking.club_id == self.club.id
            and self.can_access_court(booking.court)
        )

    @property
    def is_staff_only(self):
        return (
            self.is_staff
            and not self.is_platform_admin
            and not self.is_owner
            and not self.is_manager
        )

    def can_access_transaction(self, transaction):
        if not (
            transaction is not None
            and transaction.club_id == self.club.id
            and self.can_access_court(transaction.court)
        ):
            return False
        if self.is_staff_only:
            return transaction.created_by_id == self.user.id
        return True

    def can_cancel_transaction(self, transaction):
        if not self.can_access_transaction(transaction):
            return False
        return self.is_platform_admin or transaction.created_by_id == self.user.id

    def can_access_transaction_attempt(self, attempt):
        if not (
            attempt is not None
            and attempt.club_id == self.club.id
            and self.can_access_court(attempt.court)
        ):
            return False
        if self.is_staff_only:
            return attempt.attempted_by_id == self.user.id
        return True

    def can_dismiss_transaction_attempt(self, attempt):
        return (
            self.can_access_transaction_attempt(attempt)
            and attempt.attempted_by_id == self.user.id
        )

    def can_manage_settlements(self):
        if self.is_platform_admin or self.is_owner:
            return True
        return self.manager_can_settle_transactions

    def can_view_own_settlements(self):
        return self.is_platform_admin or bool(self.active_memberships)

    def can_preview_settlement_for_user(self, user):
        if user.id == self.user.id:
            roles = {membership.role for membership in self.active_memberships}
        else:
            roles = self.active_roles_for_user(user)
        return self.can_preview_settlement_for_roles(user_id=user.id, roles=roles)

    def can_approve_settlement_for_user(self, user):
        if user.id == self.user.id:
            roles = {membership.role for membership in self.active_memberships}
        else:
            roles = self.active_roles_for_user(user)
        return self.can_approve_settlement_for_roles(user_id=user.id, roles=roles)

    def can_create_settlement(self, court=None):
        if court is not None and court.club_id != self.club.id:
            return False
        return self.can_manage_settlements()

    def can_access_settlement(self, settlement):
        if settlement is None or settlement.club_id != self.club.id:
            return False
        if self.can_manage_settlements():
            return True
        return (
            self.can_view_own_settlements()
            and settlement.collected_by_id == self.user.id
        )

    def can_view_audit_logs(self):
        return self.is_platform_admin or self.is_owner or self.is_manager

    def can_access_audit_log(self, audit_log):
        return (
            audit_log is not None
            and audit_log.club_id == self.club.id
            and self.can_view_audit_logs()
        )

    def can_view_court_availability(self, court):
        return self.can_access_court(court)

    def can_view_calendar(self):
        return self.has_any_club_access()

    def can_view_dashboard(self):
        return self.is_platform_admin or self.is_owner or self.is_manager

    def can_view_reports(self):
        return self.is_platform_admin or self.is_owner or self.is_manager

    def can_filter_reports_by_staff(self, user):
        return self.can_view_reports() and self.user_has_active_membership(user)

    def can_view_dashboard_summary(self):
        return self.has_any_club_access()

    def can_view_financial_summary(self):
        return self.is_platform_admin or self.is_owner or self.is_manager

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

    def scoped_booking_attempts_queryset(self):
        from apps.bookings.models import BookingAttempt

        queryset = BookingAttempt.objects.filter(
            club=self.club,
            court__in=self.scoped_courts_queryset(),
        )
        if self.is_staff_only:
            queryset = queryset.filter(attempted_by=self.user)
        return queryset

    def scoped_calendar_bookings_queryset(self):
        return self.scoped_bookings_queryset()

    def scoped_dashboard_courts_queryset(self):
        from apps.courts.models import Court

        queryset = Court.objects.filter(club=self.club)
        if self.can_view_dashboard():
            return queryset
        return queryset.none()

    def scoped_report_courts_queryset(self):
        from apps.courts.models import Court

        queryset = Court.objects.filter(club=self.club)
        if self.can_view_reports():
            return queryset
        return queryset.none()

    def scoped_dashboard_summary_courts_queryset(self):
        from apps.courts.models import Court

        queryset = Court.objects.filter(club=self.club)
        if self.can_view_dashboard_summary():
            if self.can_view_financial_summary():
                return queryset
            if self.is_staff:
                return queryset.filter(id__in=self.staff_court_ids)
        return queryset.none()

    def scoped_transactions_queryset(self):
        from apps.transactions.models import Transaction

        queryset = Transaction.objects.filter(court__in=self.scoped_courts_queryset())
        if self.is_staff_only:
            queryset = queryset.filter(created_by=self.user)
        return queryset

    def scoped_transaction_attempts_queryset(self):
        from apps.transactions.models import TransactionAttempt

        queryset = TransactionAttempt.objects.filter(
            club=self.club,
            court__in=self.scoped_courts_queryset(),
        )
        if self.is_staff_only:
            queryset = queryset.filter(attempted_by=self.user)
        return queryset

    def scoped_settlements_queryset(self):
        from apps.settlements.models import Settlement

        queryset = Settlement.objects.filter(club=self.club)
        if self.can_manage_settlements():
            return queryset
        if self.can_view_own_settlements():
            return queryset.filter(collected_by=self.user)
        return queryset.none()

    def scoped_audit_logs_queryset(self):
        from apps.audit.models import AuditLog

        queryset = AuditLog.objects.filter(club=self.club)
        if self.can_view_audit_logs():
            return queryset
        return queryset.none()

    def scoped_memberships_queryset(self):
        queryset = ClubMembership.objects.current().filter(club=self.club)
        if self.can_manage_memberships():
            return queryset
        return queryset.none()

    def scoped_club_users_queryset(self):
        queryset = ClubMembership.objects.current().filter(club=self.club)
        if self.is_platform_admin or self.is_owner:
            return queryset
        if self.is_manager:
            return queryset.filter(
                role__in={
                    ClubMembership.Role.MANAGER,
                    ClubMembership.Role.STAFF,
                },
                is_active=True,
            )
        return queryset.none()
