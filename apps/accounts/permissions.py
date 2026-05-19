from rest_framework.permissions import BasePermission


class IsPlatformSuperAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user.is_authenticated and user.is_platform_super_admin())
