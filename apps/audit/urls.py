from django.urls import path

from apps.audit.views import AuditLogViewSet

audit_log_list = AuditLogViewSet.as_view({"get": "list"})
audit_log_detail = AuditLogViewSet.as_view({"get": "retrieve"})

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/audit-logs/",
        audit_log_list,
        name="club-audit-log-list",
    ),
    path(
        "clubs/<slug:club_slug>/audit-logs/<int:pk>/",
        audit_log_detail,
        name="club-audit-log-detail",
    ),
]
