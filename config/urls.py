"""URL configuration for the Sloty project."""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from apps.accounts.views import SlotyTokenObtainPairView, SlotyTokenRefreshView
from apps.common.views import HealthCheckAPIView

urlpatterns = [
    path("healthz", HealthCheckAPIView.as_view(), name="healthz"),
    path("health/", HealthCheckAPIView.as_view(), name="health_root"),
    path("health", HealthCheckAPIView.as_view()),
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.common.urls")),
    path("api/v1/", include("apps.clubs.urls")),
    path("api/v1/", include("apps.courts.urls")),
    path("api/v1/", include("apps.players.urls")),
    path("api/v1/", include("apps.bookings.urls")),
    path("api/v1/", include("apps.transactions.urls")),
    path("api/v1/", include("apps.settlements.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.dashboard.urls")),
    path("api/v1/", include("apps.reports.urls")),
    path(
        "api/v1/schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(
            url_name="schema",
            permission_classes=[AllowAny],
        ),
        name="swagger-ui",
    ),
    path(
        "api/v1/auth/token/",
        SlotyTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path(
        "api/v1/auth/token/refresh/",
        SlotyTokenRefreshView.as_view(),
        name="token_refresh",
    ),
]
