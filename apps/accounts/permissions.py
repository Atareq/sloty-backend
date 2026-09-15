from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.profiles.models import Profile


class IsPlatformSuperAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_platform_super_admin())


class CanAccessUsers(BasePermission):
    """
    Permission for /api/v1/users/:
    - Platform super admins have full CRUD access.
    - Club Owners have read-only access (GET/HEAD/OPTIONS) within their club
      staff scope.
    - All other users (Managers, Staff, standard users, anonymous) are denied.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_platform_super_admin():
            return True
        if request.method in SAFE_METHODS:
            return Profile.objects.filter(user=user, role=Profile.Role.OWNER).exists()
        return False

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_platform_super_admin():
            return True
        if request.method in SAFE_METHODS:
            return Profile.objects.filter(
                user=user,
                role=Profile.Role.OWNER,
                owner_profile__clubs__staff_profiles__profile__user=obj,
            ).exists()
        return False
