from django.urls import path

from apps.common.views import EgyptLocationAPIView, HealthCheckAPIView

urlpatterns = [
    path("egypt-locations/", EgyptLocationAPIView.as_view(), name="egypt-locations"),
    path("health/", HealthCheckAPIView.as_view(), name="health_check"),
]
