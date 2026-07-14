from django.urls import path

from apps.dashboard.views import (
    ClubCalendarAPIView,
    CourtAvailabilityAPIView,
    CourtUtilizationAPIView,
    DashboardOverviewAPIView,
    DashboardRevenueAPIView,
)

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/availability/",
        CourtAvailabilityAPIView.as_view(),
        name="club-court-availability",
    ),
    path(
        "clubs/<slug:club_slug>/calendar/",
        ClubCalendarAPIView.as_view(),
        name="club-calendar",
    ),
    path(
        "clubs/<slug:club_slug>/dashboard/overview/",
        DashboardOverviewAPIView.as_view(),
        name="club-dashboard-overview",
    ),
    path(
        "clubs/<slug:club_slug>/dashboard/revenue/",
        DashboardRevenueAPIView.as_view(),
        name="club-dashboard-revenue",
    ),
    path(
        "clubs/<slug:club_slug>/dashboard/court-utilization/",
        CourtUtilizationAPIView.as_view(),
        name="club-dashboard-court-utilization",
    ),
]
