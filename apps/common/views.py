from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.egypt_locations import get_location_payload
from apps.common.serializers import EgyptLocationPayloadSerializer


class EgyptLocationAPIView(APIView):
    permission_classes = (AllowAny,)
    serializer_class = EgyptLocationPayloadSerializer

    @extend_schema(
        tags=["Common"],
        responses=EgyptLocationPayloadSerializer,
    )
    def get(self, request):
        return Response(get_location_payload())
