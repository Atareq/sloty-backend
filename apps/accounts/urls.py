from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import MeAPIView, SyncHeartbeatAPIView, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("me/", MeAPIView.as_view(), name="me"),
    path(
        "me/sync-heartbeat/",
        SyncHeartbeatAPIView.as_view(),
        name="sync-heartbeat",
    ),
    *router.urls,
]
