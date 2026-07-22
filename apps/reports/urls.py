from django.urls import path

from apps.reports.views import CourtUsageReportAPIView

urlpatterns = [
    path(
        "clubs/<slug:club_slug>/reports/court-usage/",
        CourtUsageReportAPIView.as_view(),
        name="club-report-court-usage",
    ),
]
