from django.db import transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.mixins import (
    CreateModelMixin,
    DestroyModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.clubs.staff_serializers import (
    StaffCourtAssignmentSerializer,
    StaffCreateSerializer,
    StaffDetailSerializer,
    StaffListSerializer,
    StaffUpdateSerializer,
)
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.scopes import ResourceScope
from apps.profiles.models import StaffCourtAccess, StaffProfile


@extend_schema_view(
    list=extend_schema(tags=["Staff"], responses=StaffListSerializer),
    create=extend_schema(
        tags=["Staff"],
        request=StaffCreateSerializer,
        responses=StaffDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Staff"], responses=StaffDetailSerializer),
    partial_update=extend_schema(
        tags=["Staff"],
        request=StaffUpdateSerializer,
        responses=StaffDetailSerializer,
    ),
    destroy=extend_schema(tags=["Staff"], responses={204: None}),
)
class StaffViewSet(
    SlotyScopedResourceMixin,
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    DestroyModelMixin,
    GenericViewSet,
):
    """
    Club-scoped staff management for Owners and Admins.

    Enforces:
    - URL club scope is authoritative.
    - Owner access is bounded by owned clubs.
    - Staff members have no staff management permission (denied by matrix).
    - Lookups are by profile_id within the active club scope.
    """

    authorization_model = StaffProfile
    authorization_scope = ResourceScope.CLUB
    authorization_select_related = ("user", "club")
    authorization_prefetch_related = ("court_accesses__court",)
    permission_classes = (SlotyBasePermission,)
    lookup_field = "pk"
    lookup_url_kwarg = "profile_id"
    http_method_names = ("get", "post", "put", "patch", "delete", "head", "options")

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("id")

    def get_serializer_class(self):
        if self.action == "list":
            return StaffListSerializer
        if self.action == "create":
            return StaffCreateSerializer
        if self.action in {"partial_update", "update"}:
            return StaffUpdateSerializer
        if self.action == "courts":
            return StaffCourtAssignmentSerializer
        return StaffDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["access_context"] = self.access_context
            context["club_access"] = self.access_context
        return context

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.delete()

    @extend_schema(
        tags=["Staff"],
        request=StaffCourtAssignmentSerializer,
        responses=StaffDetailSerializer,
    )
    @action(detail=True, methods=["put"], url_path="courts")
    def courts(self, request, *args, **kwargs):
        staff_profile = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        court_ids = serializer.validated_data["court_ids"]

        with transaction.atomic():
            staff_profile = StaffProfile.objects.select_for_update().get(
                pk=staff_profile.pk
            )
            staff_profile.court_accesses.all().delete()
            for court_id in court_ids:
                StaffCourtAccess.objects.create(
                    staff_profile=staff_profile,
                    court_id=court_id,
                )

        return Response(
            StaffDetailSerializer(
                staff_profile, context=self.get_serializer_context()
            ).data
        )
