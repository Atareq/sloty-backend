from rest_framework.permissions import BasePermission

from apps.clubs.authorization import can_manage_club


class CanManageClubs(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        return view.action != "create" or request.user.is_platform_super_admin()

    def has_object_permission(self, request, view, obj) -> bool:
        return can_manage_club(request.user, obj, view.action)
