from rest_framework.permissions import BasePermission

from apps.clubs.authorization import can_manage_club
from apps.profiles.models import Profile


class CanManageClubs(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, "profile", None)
        return view.action != "create" or bool(
            profile and profile.is_enabled and profile.role == Profile.Role.ADMIN
        )

    def has_object_permission(self, request, view, obj) -> bool:
        return can_manage_club(getattr(request.user, "profile", None), obj, view.action)
