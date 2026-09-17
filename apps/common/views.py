from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.egypt_locations import get_location_payload
from apps.common.release_metadata import get_backend_release_payload
from apps.common.serializers import (
    BackendVersionSerializer,
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
    authentication_classes = ()
    permission_classes = (AllowAny,)
    serializer_class = HealthCheckResponseSerializer

    @extend_schema(
        tags=["System"],
        summary="Health Check",
        description=(
            "Unauthenticated liveness probe for systemd and production "
            "deployment verification. Independent of JWT and PostgreSQL."
        ),
        responses={200: HealthCheckResponseSerializer},
    )
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


class VersionAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = BackendVersionSerializer

    @extend_schema(
        tags=["System"],
        summary="Backend runtime version",
        description=(
            "Returns the backend release currently running on this host. "
            "Values come from deployment metadata written only after a "
            "successful deploy and health check. Missing or invalid metadata "
            "returns null release and commit. Commit is the authoritative "
            "technical identity of the running code."
        ),
        responses={200: BackendVersionSerializer},
    )
    def get(self, request):
        response = Response(get_backend_release_payload())
        response["Cache-Control"] = "no-store"
        return response
