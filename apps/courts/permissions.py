from rest_framework.permissions import BasePermission

from apps.clubs.permissions import is_active_club_manager, is_active_club_owner
from apps.courts.models import CourtStaffAssignment


def is_active_court_staff(user, court) -> bool:
    if not user.is_authenticated:
        return False
    return CourtStaffAssignment.objects.filter(
        court=court,
        user=user,
        is_active=True,
    ).exists()


def can_manage_court_setup(user, court) -> bool:
    return user.is_authenticated and (
        user.is_platform_super_admin() or is_active_club_owner(user, court.club)
    )


def can_manage_working_hours(user, court) -> bool:
    return user.is_authenticated and (
        user.is_platform_super_admin()
        or is_active_club_owner(user, court.club)
        or is_active_club_manager(user, court.club)
    )


class CanManageCourts(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if view.action == "create":
            return user.is_platform_super_admin() or user.is_club_owner()
        if view.action in {"update", "partial_update"}:
            return (
                user.is_platform_super_admin()
                or user.is_club_owner()
                or user.is_manager()
            )
        return (
            user.is_platform_super_admin()
            or user.is_club_owner()
            or user.is_manager()
            or user.is_staff_member()
        )

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if user.is_platform_super_admin():
            return True
        if view.action in {"update", "partial_update"}:
            return is_active_club_owner(user, obj.club) or is_active_club_manager(
                user, obj.club
            )
        if user.is_club_owner():
            return is_active_club_owner(user, obj.club)
        if user.is_manager():
            return is_active_club_manager(user, obj.club)
        return is_active_court_staff(user, obj)


class CanManageCourtWorkingHours(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user.is_authenticated
            and (
                user.is_platform_super_admin()
                or user.is_club_owner()
                or user.is_manager()
            )
        )

    def has_object_permission(self, request, view, obj) -> bool:
        return can_manage_working_hours(request.user, obj.court)


class CanManageCourtStaffAssignments(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if view.action in {"create", "update", "partial_update"}:
            return user.is_platform_super_admin() or user.is_club_owner()
        return (
            user.is_platform_super_admin()
            or user.is_club_owner()
            or user.is_manager()
            or user.is_staff_member()
        )

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if user.is_platform_super_admin():
            return True
        if user.is_club_owner():
            return is_active_club_owner(user, obj.court.club)
        if user.is_manager():
            return is_active_club_manager(user, obj.court.club)
        return obj.user_id == user.id
