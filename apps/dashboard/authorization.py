"""
Dashboard domain authorization the Spine cannot express as a resource model.

Dashboard is a read/aggregation domain with no persisted Dashboard model.
Ordinary Club/Court WHERE is resolved through source-domain
authorization_config + scoped_queryset:

- Bookings / operational courts / operational transactions → COURT
- Persisted Settlement totals on financial dashboards → CLUB (never collector
  narrowing; financial viewers are Admin/Owner/Manager)
- Current custody candidates → Club + optional Collector, with an optional
  explicit court query filter as a read-model filter — never Staff assignment

This module keeps:
- court-object access for availability and optional filters (HTTP 403)
- financial_visible presentation (Staff operational summary, financial nulls)
- dashboard scope.role labels
"""

from apps.bookings.models import Booking
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from apps.courts.models import Court
from apps.settlements.models import Settlement
from apps.transactions.models import Transaction


def can_access_court(context: RequestAccessContext, court) -> bool:
    """True when the court is inside the actor's Spine court queryset."""
    if court is None or getattr(context, "club", None) is None:
        return False
    if court.club_id != context.club.id:
        return False
    return (
        scoped_queryset(context, Court, scope=ResourceScope.COURT)
        .filter(pk=court.pk)
        .exists()
    )


def can_view_financial_summary(context: RequestAccessContext) -> bool:
    """Staff see operational summary only; financial fields stay null."""
    return context.is_platform_admin or context.role == Role.OWNER


def is_staff_collector(context: RequestAccessContext) -> bool:
    return context.role == Role.STAFF and not context.is_platform_admin


def dashboard_summary_role(context: RequestAccessContext) -> str:
    if context.is_platform_admin:
        return "PLATFORM_ADMIN"
    if context.role == Role.OWNER:
        return "OWNER"
    if context.role == Role.STAFF:
        return "STAFF"
    return "NONE"


def scoped_dashboard_courts(context: RequestAccessContext):
    """Operational/financial court rows: Club + Court (Staff assigned courts)."""
    return scoped_queryset(context, Court, scope=ResourceScope.COURT)


def scoped_dashboard_bookings(context: RequestAccessContext):
    """Booking aggregations follow the Booking Club + Court boundary."""
    return scoped_queryset(context, Booking, scope=ResourceScope.COURT)


def scoped_dashboard_transactions(context: RequestAccessContext):
    """
    Operational transaction aggregations follow Club + Court.

    Dashboard does not apply Transaction Staff created_by narrowing. Financial
    endpoints are matrix-denied for Staff; remaining callers are club-wide
    financial roles.
    """
    return scoped_queryset(context, Transaction, scope=ResourceScope.COURT)


def scoped_dashboard_settlements(context: RequestAccessContext):
    """
    Settled-settlement totals on financial dashboards are club-scoped.

    Do not apply Settlement collector narrowing or court assignment. An
    explicit court query parameter remains a read-model filter in services.
    """
    return scoped_queryset(context, Settlement, scope=ResourceScope.CLUB)
