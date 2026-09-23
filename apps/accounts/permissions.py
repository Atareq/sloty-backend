from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.profiles.models import Profile


class IsPlatformSuperAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        profile = getattr(user, "profile", None)
        return bool(
            user.is_authenticated
            and profile
            and profile.is_enabled
            and profile.role == Profile.Role.ADMIN
        )


class CanAccessUsers(BasePermission):
    """
    Permission for /api/v1/users/:
    - Platform super admins have full CRUD access.
    - Club Owners have read access and may PATCH ``is_active`` within their
      owned-club staff scope.
    - All other users (Staff, standard users, anonymous) are denied.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        profile = getattr(user, "profile", None)
        if profile and profile.is_enabled and profile.role == Profile.Role.ADMIN:
            return True
        if request.method in SAFE_METHODS or request.method == "PATCH":
            return Profile.objects.filter(
                user=user,
                role=Profile.Role.OWNER,
                is_enabled=True,
            ).exists()
        return False

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        profile = getattr(user, "profile", None)
        if profile and profile.is_enabled and profile.role == Profile.Role.ADMIN:
            return True
        if request.method in SAFE_METHODS or request.method == "PATCH":
            return Profile.objects.filter(
                user=user,
                role=Profile.Role.OWNER,
                is_enabled=True,
                owner_profile__clubs__staff_profiles__user=obj,
                owner_profile__clubs__staff_profiles__is_enabled=True,
            ).exists()
        return False
