from rest_framework.permissions import BasePermission

from apps.clubs.models import ClubMembership


def is_active_club_owner(user, club) -> bool:
    if not user.is_authenticated:
        return False
    return ClubMembership.objects.filter(
        club=club,
        user=user,
        role=ClubMembership.Role.OWNER,
        is_active=True,
    ).exists()


def is_active_club_manager(user, club) -> bool:
    if not user.is_authenticated:
        return False
    return ClubMembership.objects.filter(
        club=club,
        user=user,
        role=ClubMembership.Role.MANAGER,
        is_active=True,
    ).exists()


class CanManageClubs(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if view.action == "create":
            return user.is_platform_super_admin()
        if view.action in {"update", "partial_update"}:
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
        if view.action in {"update", "partial_update"}:
            return is_active_club_owner(user, obj)
        if user.is_club_owner():
            return is_active_club_owner(user, obj)
        if user.is_manager():
            return is_active_club_manager(user, obj)
        if user.is_staff_member():
            from apps.courts.models import CourtStaffAssignment

            return CourtStaffAssignment.objects.filter(
                court__club=obj,
                user=user,
                is_active=True,
            ).exists()
        return False


class CanManageClubMemberships(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user.is_authenticated
            and (user.is_platform_super_admin() or user.is_club_owner())
        )

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if user.is_platform_super_admin():
            return True
        if view.action in {"update", "partial_update"}:
            return obj.role == ClubMembership.Role.MANAGER and is_active_club_owner(
                user, obj.club
            )
        return is_active_club_owner(user, obj.club)
