from rest_framework.permissions import BasePermission

from apps.clubs.permissions import CanManageClubBookings


class CanManageBookingAttempts(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().has_any_club_access()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        if view.action == "dismiss":
            return access.can_dismiss_booking_attempt(obj)
        return access.can_access_booking_attempt(obj)


__all__ = ["CanManageBookingAttempts", "CanManageClubBookings"]
