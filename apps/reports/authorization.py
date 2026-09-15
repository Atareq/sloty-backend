"""
Reports domain authorization the Spine cannot express as a resource model.

Court Usage Report is a read/aggregation endpoint with no Report table.
Ordinary Club/Court WHERE is resolved through source-domain
authorization_config + scoped_queryset:

- Courts / Bookings → COURT (Admin/Owner/Manager see all club courts)
- Paid amounts are annotations on those authorized bookings. Do not apply
  Transaction Staff created_by narrowing or Settlement collector narrowing.
- There is no Settlement/custody dataset on this report.

This module keeps:
- court-object access for the optional court filter (HTTP 403)
- staff-membership validation for the optional created_by filter
- source querysets used before aggregation
"""

from apps.bookings.models import Booking
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.scopes import ResourceScope
from apps.courts.models import Court
from apps.profiles.models import Profile


def can_access_report_court(context: RequestAccessContext, court) -> bool:
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


def can_filter_reports_by_staff(context: RequestAccessContext, user) -> bool:
    """
    Optional created_by filter may only name a user with active access in
    this club. This is a report filter rule, not a collector security scope.
    """
    if user is None or getattr(context, "club", None) is None:
        return False
    profile = Profile.objects.filter(user=user).first()
    if profile is None:
        return False
    if profile.role == Profile.Role.ADMIN:
        return True
    if profile.role == Profile.Role.OWNER:
        return profile.owner_profile.clubs.filter(pk=context.club.pk).exists()
    if profile.role == Profile.Role.STAFF:
        return profile.staff_profile.court.club_id == context.club.pk
    return False


def scoped_report_courts(context: RequestAccessContext):
    """Court Usage courts follow the Court Club + Court boundary."""
    return scoped_queryset(context, Court, scope=ResourceScope.COURT)


def scoped_report_bookings(context: RequestAccessContext):
    """
    Occupancy and booking-value aggregations follow Booking Club + Court.

    Not creator-scoped. Staff are matrix-denied before this queryset is used.
    """
    return scoped_queryset(context, Booking, scope=ResourceScope.COURT)
