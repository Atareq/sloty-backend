from django.urls import path

from apps.courts.views import CourtViewSet, CourtWorkingHourViewSet

court_list = CourtViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
court_detail = CourtViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)
working_hour_list = CourtWorkingHourViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
working_hour_detail = CourtWorkingHourViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("clubs/<slug:club_slug>/courts/", court_list, name="club-court-list"),
    path(
        "clubs/<slug:club_slug>/courts/<int:pk>/",
        court_detail,
        name="club-court-detail",
    ),
    path(
        "clubs/<slug:club_slug>/court-working-hours/",
        working_hour_list,
        name="club-court-working-hour-list",
    ),
    path(
        "clubs/<slug:club_slug>/court-working-hours/<int:pk>/",
        working_hour_detail,
        name="club-court-working-hour-detail",
    ),
]
