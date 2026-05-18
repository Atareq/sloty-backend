from rest_framework.routers import DefaultRouter

from apps.clubs.views import ClubMembershipViewSet, ClubViewSet

router = DefaultRouter()
router.register("clubs", ClubViewSet, basename="club")
router.register(
    "club-memberships",
    ClubMembershipViewSet,
    basename="club-membership",
)

urlpatterns = router.urls
