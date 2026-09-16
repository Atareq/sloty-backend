from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.clubs.staff_views import StaffViewSet
from apps.clubs.views import ClubViewSet

router = DefaultRouter()
router.register("clubs", ClubViewSet, basename="club")

urlpatterns = router.urls
staff_list = StaffViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
staff_detail = StaffViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
        "delete": "destroy",
    }
)
staff_courts = StaffViewSet.as_view(
    {
        "put": "courts",
    }
)

urlpatterns = router.urls + [
    path("clubs/<slug:club_slug>/staff/", staff_list, name="club-staff-list"),
    path(
        "clubs/<slug:club_slug>/staff/<int:profile_id>/",
        staff_detail,
        name="club-staff-detail",
    ),
    path(
        "clubs/<slug:club_slug>/staff/<int:profile_id>/courts/",
        staff_courts,
        name="club-staff-courts",
    ),
]
