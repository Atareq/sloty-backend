from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.egypt_locations import get_location_payload
from apps.common.serializers import (
    EgyptLocationPayloadSerializer,
    HealthCheckResponseSerializer,
)


class EgyptLocationAPIView(APIView):
    permission_classes = (AllowAny,)
    serializer_class = EgyptLocationPayloadSerializer

    @extend_schema(
        tags=["Common"],
        responses=EgyptLocationPayloadSerializer,
    )
    def get(self, request):
        return Response(get_location_payload())


class HealthCheckAPIView(APIView):
    permission_classes = (AllowAny,)
    serializer_class = HealthCheckResponseSerializer

    @extend_schema(
        tags=["System"],
        summary="Health Check",
        description="Health check endpoint for container orchestration and deployment liveness/readiness probes.",
        responses={
            200: HealthCheckResponseSerializer,
            503: HealthCheckResponseSerializer,
        },
    )
    def get(self, request):
        db_status = "connected"
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_status = "unavailable"
            return Response(
                {"status": "error", "database": db_status},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"status": "ok", "database": db_status},
            status=status.HTTP_200_OK,
        )
