from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.clubs.views import ClubMembershipViewSet, ClubViewSet

router = DefaultRouter()
router.register("clubs", ClubViewSet, basename="club")

membership_list = ClubMembershipViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
membership_detail = ClubMembershipViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
    }
)

urlpatterns = [
    *router.urls,
    path(
        "clubs/<slug:club_slug>/memberships/",
        membership_list,
        name="club-membership-list",
    ),
    path(
        "clubs/<slug:club_slug>/memberships/<int:pk>/",
        membership_detail,
        name="club-membership-detail",
    ),
]
