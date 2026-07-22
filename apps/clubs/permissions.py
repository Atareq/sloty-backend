from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.clubs.models import ClubMembership


def has_active_club_membership(user, club) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_platform_super_admin():
        return True
    return ClubMembership.objects.filter(
        club=club,
        user=user,
        is_active=True,
    ).exists()


def has_active_owner_membership(user, club) -> bool:
    if not user.is_authenticated:
        return False
    return ClubMembership.objects.filter(
        club=club,
        user=user,
        role=ClubMembership.Role.OWNER,
        is_active=True,
    ).exists()


class CanManageClubs(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if view.action == "create":
            return user.is_platform_super_admin()
        return True

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not user.is_authenticated:
            return False
        if user.is_platform_super_admin():
            return True
        if view.action in {"update", "partial_update"}:
            return has_active_owner_membership(user, obj)
        return has_active_club_membership(user, obj)


class HasClubAccess(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().has_any_club_access()


class CanManageClubMemberships(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_manage_memberships()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        if obj.club_id != access.club.id:
            return False
        if request.method in SAFE_METHODS:
            return access.can_manage_memberships()
        if access.is_platform_admin:
            return True
        return access.is_owner and obj.role != ClubMembership.Role.OWNER


class CanListClubUsers(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_list_club_users()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        return obj.club_id == access.club.id and access.can_list_club_users()


class CanManageClubCourts(BasePermission):
    def has_permission(self, request, view) -> bool:
        access = view.get_access_context()
        if view.action == "create":
            return access.can_create_court()
        return access.has_any_club_access()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        if request.method in SAFE_METHODS:
            return access.can_access_court(obj)
        if view.action in {"update", "partial_update"}:
            return access.is_platform_admin or access.is_owner or access.is_manager
        return access.can_access_court(obj)


class CanManageClubWorkingHours(BasePermission):
    def has_permission(self, request, view) -> bool:
        access = view.get_access_context()
        if view.action in {"create", "update", "partial_update"}:
            return access.is_platform_admin or access.is_owner or access.is_manager
        return access.has_any_club_access()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        if request.method in SAFE_METHODS:
            return access.can_access_court(obj.court)
        return access.can_manage_working_hours(obj.court)


class CanManageClubBookings(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().has_any_club_access()

    def has_object_permission(self, request, view, obj) -> bool:
        access = view.get_access_context()
        return obj.club_id == access.club.id and access.can_access_court(obj.court)


class CanManageClubSettlements(BasePermission):
    def has_permission(self, request, view) -> bool:
        access = view.get_access_context()
        if view.action in {"create", "preview"}:
            return access.has_any_club_access()
        return access.can_manage_settlements()

    def has_object_permission(self, request, view, obj) -> bool:
        return view.get_access_context().can_access_settlement(obj)


class CanViewClubAuditLogs(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_view_audit_logs()

    def has_object_permission(self, request, view, obj) -> bool:
        return view.get_access_context().can_access_audit_log(obj)


class CanViewClubDashboard(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_view_dashboard()


class CanViewClubReports(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_view_reports()


class CanViewDashboardSummary(BasePermission):
    def has_permission(self, request, view) -> bool:
        return view.get_access_context().can_view_dashboard_summary()
