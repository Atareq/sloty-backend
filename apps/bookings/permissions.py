from rest_framework.permissions import BasePermission

from apps.clubs.permissions import CanManageClubBookings


class CanManageBookingAttempts(BasePermission):
    """
    Legacy object-permission class. BookingAttemptViewSet now uses
    SlotyBasePermission + Spine queryset + check_object_permission.
    Kept for Phase C cleanup once no compatibility imports remain.
    """

    def has_permission(self, request, view) -> bool:
        return view.get_access_context().has_any_club_access()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        if view.action == "dismiss":
            return access.can_dismiss_booking_attempt(obj)
        return access.can_access_booking_attempt(obj)


__all__ = ["CanManageBookingAttempts", "CanManageClubBookings"]
