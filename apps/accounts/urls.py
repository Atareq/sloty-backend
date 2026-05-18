from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import MeAPIView, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("me/", MeAPIView.as_view(), name="me"),
    *router.urls,
]
