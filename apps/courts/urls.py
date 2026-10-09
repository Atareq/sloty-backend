from django.urls import path

from apps.courts.views import (
    CourtImageViewSet,
    CourtViewSet,
    CourtWeeklyWorkingHoursViewSet,
    CourtWorkingHourViewSet,
)

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
nested_working_hour_list = CourtWeeklyWorkingHoursViewSet.as_view(
    {
        "get": "list",
        "put": "update",
    }
)
court_image_list = CourtImageViewSet.as_view({"get": "list", "post": "create"})
court_image_detail = CourtImageViewSet.as_view({"delete": "destroy"})
court_image_reorder = CourtImageViewSet.as_view({"put": "reorder"})

urlpatterns = [
    path("clubs/<slug:club_slug>/courts/", court_list, name="club-court-list"),
    path(
        "clubs/<slug:club_slug>/courts/<int:pk>/",
        court_detail,
        name="club-court-detail",
    ),
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/working-hours/",
        nested_working_hour_list,
        name="club-court-nested-working-hour-list",
    ),
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/images/",
        court_image_list,
        name="club-court-image-list",
    ),
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/images/reorder/",
        court_image_reorder,
        name="club-court-image-reorder",
    ),
    path(
        "clubs/<slug:club_slug>/courts/<int:court_id>/images/<int:court_image_id>/",
        court_image_detail,
        name="club-court-image-detail",
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
