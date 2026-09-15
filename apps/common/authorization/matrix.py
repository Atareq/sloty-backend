"""
Centralized Role -> ViewSet -> DRF Action Permission Matrix.

ARCHITECTURAL INVARIANTS:
1. Pure Role -> ViewSet -> Action mapping. No capability abstractions.
2. Only policy entries supported by verified repository contracts and tests
   are populated.
3. Default Deny: If role, viewset, or action is unconfigured, access is strictly denied.
4. Global tenant endpoint note: ClubViewSet (/api/v1/clubs/) serves as the
   platform-level club onboarding & tenant discovery endpoint governed by
   CanManageClubs and scoped_clubs_for_user. Its entries in ROLE_PERMISSIONS reflect
   this policy for uniform reference and testing.
"""

from typing import Dict, Set

from apps.common.authorization.roles import Role

# Operational Booking API actions shared by every club role. Admin also
# retains unused update/destroy matrix entries for the ViewSet name; the
# ViewSet itself only exposes GET/POST/PATCH.
_BOOKING_VIEWSET_ACTIONS = {
    "list",
    "retrieve",
    "create",
    "partial_update",
    "cancel",
    "complete",
    "no_show",
    "reschedule",
    "expire",
    "end_recurrence",
    "slots",
    "recurrence_next",
    "cancellation_preview",
}

ROLE_PERMISSIONS: Dict[str, Dict[str, Set[str]]] = {
    Role.ADMIN: {
        "ClubViewSet": {
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "destroy",
        },
        "ClubUserListViewSet": {"list"},
        "CourtViewSet": {
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "destroy",
        },
        "CourtWeeklyWorkingHoursViewSet": {
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "destroy",
        },
        "BookingViewSet": _BOOKING_VIEWSET_ACTIONS | {"update", "destroy"},
        "BookingAttemptViewSet": {"list", "retrieve", "dismiss"},
        "TransactionViewSet": {"list", "retrieve", "create", "cancel"},
        "TransactionAttemptViewSet": {"list", "retrieve", "dismiss"},
        "SettlementViewSet": {
            "list",
            "retrieve",
            "create",
            "preview",
            "unsettled_summary",
            "mark_settled",
        },
        "AuditLogViewSet": {"list", "retrieve"},
        "DashboardViewSet": {
            "summary",
            "overview",
            "revenue",
            "court_utilization",
            "calendar",
            "availability",
        },
        "CourtUsageReportViewSet": {"list", "retrieve"},
        "PlayerProfileViewSet": {"list", "retrieve", "create"},
        "ClubPlayerViewSet": {"list", "retrieve", "create"},
    },
    Role.OWNER: {
        "ClubViewSet": {"list", "retrieve", "update", "partial_update"},
        "ClubUserListViewSet": {"list"},
        "CourtViewSet": {
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "destroy",
        },
        "CourtWeeklyWorkingHoursViewSet": {
            "list",
            "retrieve",
            "create",
            "update",
            "partial_update",
            "destroy",
        },
        "BookingViewSet": set(_BOOKING_VIEWSET_ACTIONS),
        "BookingAttemptViewSet": {"list", "retrieve", "dismiss"},
        "TransactionViewSet": {"list", "retrieve", "create", "cancel"},
        "TransactionAttemptViewSet": {"list", "retrieve", "dismiss"},
        "SettlementViewSet": {
            "list",
            "retrieve",
            "create",
            "preview",
            "unsettled_summary",
            "mark_settled",
        },
        "AuditLogViewSet": {"list", "retrieve"},
        "DashboardViewSet": {
            "summary",
            "overview",
            "revenue",
            "court_utilization",
            "calendar",
            "availability",
        },
        "CourtUsageReportViewSet": {"list", "retrieve"},
        "PlayerProfileViewSet": {"list", "retrieve", "create"},
        "ClubPlayerViewSet": {"list", "retrieve", "create"},
    },
    Role.STAFF: {
        "ClubViewSet": {"list", "retrieve"},
        "CourtViewSet": {"list", "retrieve"},
        "CourtWeeklyWorkingHoursViewSet": {"list", "retrieve"},
        "BookingViewSet": set(_BOOKING_VIEWSET_ACTIONS),
        "BookingAttemptViewSet": {"list", "retrieve", "dismiss"},
        "TransactionViewSet": {"list", "retrieve", "create", "cancel"},
        "TransactionAttemptViewSet": {"list", "retrieve", "dismiss"},
        "SettlementViewSet": {"list", "retrieve", "preview"},
        "DashboardViewSet": {"summary", "calendar", "availability"},
        "PlayerProfileViewSet": {"list", "retrieve", "create"},
        "ClubPlayerViewSet": {"list", "retrieve", "create"},
    },
}


def is_action_allowed(role: str, viewset_name: str, action: str) -> bool:
    """
    Evaluate allow / deny from the centralized role matrix.

    Returns True ONLY if the action is explicitly permitted for the role
    on the given ViewSet.
    Strictly defaults to False (deny).
    """
    if not role or not viewset_name or not action:
        return False
    viewset_permissions = ROLE_PERMISSIONS.get(role, {}).get(viewset_name, set())
    return action in viewset_permissions
