"""
Transaction domain authorization and boundary scope guards.

ARCHITECTURAL INVARIANTS:
1. Operational Transaction authorization is strictly scoped by CLUB + authorized COURT.
   For Staff, access is restricted to their assigned court(s) within the club,
   and staff can only see/cancel transactions created by themselves.
2. Financial custody (Settlement domain) is scoped by CLUB + optional COLLECTOR
   (never Court). These two domains have completely separate authorization boundaries:
   - Operational Transactions: Court-scoped access control for field staff.
   - Current Custody: Club + Collector cash accountability across all courts.
3. Authorization happens BEFORE transaction financial processing. Pure financial
   operations receive already-authorized domain entities.
4. Authorization functions consume RequestAccessContext facts only.
"""

from typing import Optional, Set

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.clubs.models import ClubMembership
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.transactions.models import Transaction, TransactionAttempt


def get_staff_court_ids(context: RequestAccessContext) -> Set[int]:
    """
    Return the set of court IDs the staff member is authorized for in this club.

    Uses context.membership.court_id when available without additional queries.
    Falls back to querying active granting-access staff memberships for the club.
    """
    if (
        context.membership
        and context.membership.role == Role.STAFF
        and context.membership.court_id
    ):
        return {context.membership.court_id}

    return set(
        ClubMembership.objects.granting_access()
        .filter(
            club=context.club,
            user=context.user,
            role=ClubMembership.Role.STAFF,
        )
        .exclude(court_id__isnull=True)
        .values_list("court_id", flat=True)
    )


def can_access_court(context: RequestAccessContext, court: Optional[Court]) -> bool:
    """
    Determine whether context actor is authorized to access the given court.

    Rules:
    - Court must belong to the context club.
    - Platform Admin, Owner, and Manager have access to all courts in the club.
    - Staff members have access ONLY to their assigned court(s).
    """
    if court is None or court.club_id != context.club.id:
        return False
    if context.is_platform_admin or context.role in {
        Role.ADMIN,
        Role.OWNER,
        Role.MANAGER,
    }:
        return True
    if context.role == Role.STAFF:
        return court.id in get_staff_court_ids(context)
    return False


def can_create_transaction_for_booking(
    context: RequestAccessContext, booking: Optional[Booking]
) -> bool:
    """
    Determine whether context actor may record a transaction for the given booking.

    Rules:
    - Booking must belong to the context club.
    - Context actor must have access to the booking's court.
    """
    return (
        booking is not None
        and booking.club_id == context.club.id
        and can_access_court(context, booking.court)
    )


def can_access_transaction(
    context: RequestAccessContext, transaction: Optional[Transaction]
) -> bool:
    """
    Evaluate object-level read permission for a Transaction.

    Rules:
    - Transaction must belong to the context club.
    - Transaction court must be accessible to the context actor.
    - Staff members can only access transactions they created.
    - Admin, Owner, and Manager can access all club transactions.
    """
    if not (
        transaction is not None
        and transaction.club_id == context.club.id
        and can_access_court(context, transaction.court)
    ):
        return False
    if context.role == Role.STAFF:
        return transaction.created_by_id == context.user.id
    return True


def can_cancel_transaction(
    context: RequestAccessContext, transaction: Optional[Transaction]
) -> bool:
    """
    Evaluate object-level cancellation permission for a Transaction.

    Rules:
    - Must satisfy can_access_transaction.
    - Platform Admin can cancel any transaction in the club.
    - Owner, Manager, and Staff can cancel ONLY transactions they created.
    """
    if not can_access_transaction(context, transaction):
        return False
    return context.is_platform_admin or transaction.created_by_id == context.user.id


def can_access_transaction_attempt(
    context: RequestAccessContext, attempt: Optional[TransactionAttempt]
) -> bool:
    """
    Evaluate object-level read permission for a TransactionAttempt.

    Rules:
    - Attempt must belong to the context club.
    - Attempt court must be accessible to the context actor.
    - Staff members can only access attempts they initiated.
    - Admin, Owner, and Manager can access all club attempts.
    """
    if not (
        attempt is not None
        and attempt.club_id == context.club.id
        and can_access_court(context, attempt.court)
    ):
        return False
    if context.role == Role.STAFF:
        return attempt.attempted_by_id == context.user.id
    return True


def can_dismiss_transaction_attempt(
    context: RequestAccessContext, attempt: Optional[TransactionAttempt]
) -> bool:
    """
    Evaluate permission to dismiss a failed TransactionAttempt.

    Rules:
    - Must satisfy can_access_transaction_attempt.
    - Only the user who attempted the transaction may dismiss it.
    """
    return (
        can_access_transaction_attempt(context, attempt)
        and attempt.attempted_by_id == context.user.id
    )


def scoped_transactions_queryset(
    context: RequestAccessContext,
) -> QuerySet:
    """
    Return the operational transaction queryset authorized for the context.

    - Admin, Owner, Manager: all transactions in the club.
    - Staff: transactions in the club on assigned court(s) created by the staff user.
    """
    queryset = Transaction.objects.filter(club=context.club)
    if context.is_platform_admin or context.role in {
        Role.ADMIN,
        Role.OWNER,
        Role.MANAGER,
    }:
        return queryset
    if context.role == Role.STAFF:
        court_ids = get_staff_court_ids(context)
        return queryset.filter(court_id__in=court_ids, created_by=context.user)
    return queryset.none()


def scoped_transaction_attempts_queryset(
    context: RequestAccessContext,
) -> QuerySet:
    """
    Return the transaction attempts queryset authorized for the context.

    - Admin, Owner, Manager: all transaction attempts in the club.
    - Staff: transaction attempts in the club on assigned court(s) initiated by
      the staff user.
    """
    queryset = TransactionAttempt.objects.filter(club=context.club)
    if context.is_platform_admin or context.role in {
        Role.ADMIN,
        Role.OWNER,
        Role.MANAGER,
    }:
        return queryset
    if context.role == Role.STAFF:
        court_ids = get_staff_court_ids(context)
        return queryset.filter(court_id__in=court_ids, attempted_by=context.user)
    return queryset.none()


def validate_create_transaction_authority(
    *, context: RequestAccessContext, booking: Booking
) -> None:
    """
    Validate boundary authority to create a transaction for a booking.
    """
    if booking.club_id != context.club.id:
        from apps.transactions.services import TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE

        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="TRANSACTION_BOOKING_NOT_IN_CLUB",
            message=TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE,
        )
    if not can_create_transaction_for_booking(context, booking):
        raise PermissionDenied("You cannot create transactions for this booking.")


def validate_cancel_authority(
    *, context: RequestAccessContext, transaction: Transaction
) -> None:
    """
    Validate boundary authority to cancel a transaction.
    """
    if not can_cancel_transaction(context, transaction):
        raise PermissionDenied("You do not have permission to cancel this transaction.")


def validate_dismiss_attempt_authority(
    *, context: RequestAccessContext, attempt: TransactionAttempt
) -> None:
    """
    Validate boundary authority to dismiss a transaction attempt.
    """
    if not can_dismiss_transaction_attempt(context, attempt):
        raise PermissionDenied(
            "You do not have permission to dismiss this payment attempt."
        )
