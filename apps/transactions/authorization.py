"""
Transaction domain authorization invariants the Spine cannot express.

ARCHITECTURAL INVARIANTS:
1. Ordinary Club + Court WHERE belongs to Authorization Spine v2
   (Transaction / TransactionAttempt.authorization_config + scoped_queryset).
2. Staff collector visibility (created_by / attempted_by) is a domain
   business rule applied in ViewSet.filter_scoped_queryset().
3. Cancellation: Platform Admin may cancel any in-scope row; Owner, Manager,
   and Staff may cancel only rows they collected (created_by).
4. Dismiss: only the attempter may dismiss a TransactionAttempt.
5. Create: the target Booking must be inside the actor's Spine court queryset.
   That reuses Booking.authorization_config rather than re-implementing court
   assignment. Create is NOT creator-scoped.
6. Financial custody (Settlement) is Club + optional Collector, NEVER Court.
   Do not reuse this module for settlements.
"""

from typing import Optional

from django.db.models import QuerySet
from rest_framework import status
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from apps.common.exceptions import SlotyAPIException
from apps.transactions.models import Transaction, TransactionAttempt


def _is_staff_collector(context: RequestAccessContext) -> bool:
    return context.role == Role.STAFF and not context.is_platform_admin


def apply_staff_collector_scope(
    context: RequestAccessContext,
    queryset: QuerySet,
    *,
    actor_field: str,
) -> QuerySet:
    """Narrow an already court-scoped queryset to the Staff member's own rows."""
    if _is_staff_collector(context):
        return queryset.filter(**{actor_field: context.user})
    return queryset


def can_create_transaction_for_booking(
    context: RequestAccessContext, booking: Optional[Booking]
) -> bool:
    """
    True when the booking is inside the actor's Spine club+court queryset.

    Staff may record a payment on any booking on their assigned court(s);
    this is not created_by scoping.
    """
    if booking is None or getattr(context, "club", None) is None:
        return False
    if booking.club_id != context.club.id:
        return False
    return (
        scoped_queryset(context, Booking, scope=ResourceScope.COURT)
        .filter(pk=booking.pk)
        .exists()
    )


def can_cancel_transaction(
    context: RequestAccessContext, transaction: Optional[Transaction]
) -> bool:
    """
    Object-level cancel rule after the row is already in the authorized queryset.

    Platform Admin may cancel any in-scope transaction. Owner, Manager, and
    Staff may cancel only transactions they collected.
    """
    if transaction is None or getattr(context, "club", None) is None:
        return False
    if transaction.club_id != context.club.id:
        return False
    return context.is_platform_admin or transaction.created_by_id == context.user.id


def can_dismiss_transaction_attempt(
    context: RequestAccessContext, attempt: Optional[TransactionAttempt]
) -> bool:
    """Only the user who attempted the payment may dismiss the attempt."""
    return (
        attempt is not None
        and getattr(context, "club", None) is not None
        and attempt.club_id == context.club.id
        and attempt.attempted_by_id == context.user.id
    )


def validate_create_transaction_authority(
    *, context: RequestAccessContext, booking: Booking
) -> None:
    if booking.club_id != context.club.id:
        from apps.transactions.services import TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE

        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="TRANSACTION_BOOKING_NOT_IN_CLUB",
            message=TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE,
        )
    if not can_create_transaction_for_booking(context, booking):
        raise PermissionDenied("You cannot create transactions for this booking.")
