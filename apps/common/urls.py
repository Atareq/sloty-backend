from django.urls import path

from apps.common.views import EgyptLocationAPIView

urlpatterns = [
    path("egypt-locations/", EgyptLocationAPIView.as_view(), name="egypt-locations"),
]
