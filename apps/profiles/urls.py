from rest_framework.routers import DefaultRouter

from apps.profiles.views import OwnerViewSet

router = DefaultRouter()
router.register("owners", OwnerViewSet, basename="owner")

urlpatterns = router.urls
