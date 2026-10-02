from django.urls import path

from apps.reports.views import CourtUsageReportAPIView, MonthlyCourtStatisticsAPIView

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/monthly-statistics/",
        MonthlyCourtStatisticsAPIView.as_view(),
        name="club-court-monthly-statistics",
    ),
    path(
        "clubs/<slug:club_slug>/reports/court-usage/",
        CourtUsageReportAPIView.as_view(),
        name="club-report-court-usage",
    ),
]
