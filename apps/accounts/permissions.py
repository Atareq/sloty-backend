from rest_framework.permissions import BasePermission


class IsPlatformSuperAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_platform_super_admin())


class IsClubOwner(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_club_owner())


class IsManager(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_manager())


class IsStaffMember(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_staff_member())
