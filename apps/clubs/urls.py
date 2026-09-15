from rest_framework.routers import DefaultRouter

from apps.clubs.views import ClubViewSet

router = DefaultRouter()
router.register("clubs", ClubViewSet, basename="club")

urlpatterns = router.urls
