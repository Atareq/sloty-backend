from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.viewsets import GenericViewSet

from apps.clubs.models import ClubMembership
from apps.courts.models import Court, CourtStaffAssignment, CourtWorkingHour
from apps.courts.permissions import (
    CanManageCourts,
    CanManageCourtStaffAssignments,
    CanManageCourtWorkingHours,
)
from apps.courts.serializers import (
    CourtCreateSerializer,
    CourtDetailSerializer,
    CourtListSerializer,
    CourtStaffAssignmentSerializer,
    CourtUpdateSerializer,
    CourtWorkingHourSerializer,
)


def scoped_courts_for_user(user):
    if not user.is_authenticated:
        return Court.objects.none()
    if user.is_platform_super_admin():
        return Court.objects.all()

    scope_filter = Q()
    if user.is_club_owner():
        scope_filter |= Q(
            club__memberships__user=user,
            club__memberships__role=ClubMembership.Role.OWNER,
            club__memberships__is_active=True,
        )
    if user.is_manager():
        scope_filter |= Q(
            club__memberships__user=user,
            club__memberships__role=ClubMembership.Role.MANAGER,
            club__memberships__is_active=True,
        )
    if user.is_staff_member():
        scope_filter |= Q(
            staff_assignments__user=user,
            staff_assignments__is_active=True,
        )

    if not scope_filter:
        return Court.objects.none()
    return Court.objects.filter(scope_filter).distinct()


@extend_schema_view(
    list=extend_schema(tags=["Courts"], responses=CourtListSerializer),
    create=extend_schema(
        tags=["Courts"],
        request=CourtCreateSerializer,
        responses=CourtDetailSerializer,
    ),
    retrieve=extend_schema(tags=["Courts"], responses=CourtDetailSerializer),
    partial_update=extend_schema(
        tags=["Courts"],
        request=CourtUpdateSerializer,
        responses=CourtDetailSerializer,
    ),
)
class CourtViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = (CanManageCourts,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        return (
            scoped_courts_for_user(self.request.user)
            .select_related("club", "created_by")
            .prefetch_related("working_hours")
            .order_by("id")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return CourtListSerializer
        if self.action == "create":
            return CourtCreateSerializer
        if self.action in {"partial_update", "update"}:
            return CourtUpdateSerializer
        return CourtDetailSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema_view(
    list=extend_schema(tags=["Courts"], responses=CourtWorkingHourSerializer),
    create=extend_schema(
        tags=["Courts"],
        request=CourtWorkingHourSerializer,
        responses=CourtWorkingHourSerializer,
    ),
    retrieve=extend_schema(tags=["Courts"], responses=CourtWorkingHourSerializer),
    partial_update=extend_schema(
        tags=["Courts"],
        request=CourtWorkingHourSerializer,
        responses=CourtWorkingHourSerializer,
    ),
)
class CourtWorkingHourViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    serializer_class = CourtWorkingHourSerializer
    permission_classes = (CanManageCourtWorkingHours,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        return CourtWorkingHour.objects.filter(
            court__in=scoped_courts_for_user(self.request.user)
        ).select_related("court", "court__club")


@extend_schema_view(
    list=extend_schema(tags=["Courts"], responses=CourtStaffAssignmentSerializer),
    create=extend_schema(
        tags=["Courts"],
        request=CourtStaffAssignmentSerializer,
        responses=CourtStaffAssignmentSerializer,
    ),
    retrieve=extend_schema(tags=["Courts"], responses=CourtStaffAssignmentSerializer),
    partial_update=extend_schema(
        tags=["Courts"],
        request=CourtStaffAssignmentSerializer,
        responses=CourtStaffAssignmentSerializer,
    ),
)
class CourtStaffAssignmentViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    serializer_class = CourtStaffAssignmentSerializer
    permission_classes = (CanManageCourtStaffAssignments,)
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        user = self.request.user
        queryset = CourtStaffAssignment.objects.select_related(
            "court",
            "court__club",
            "user",
            "created_by",
        ).order_by("id")
        if not user.is_authenticated:
            return queryset.none()
        if user.is_platform_super_admin():
            return queryset
        if user.is_club_owner():
            return queryset.filter(
                court__club__memberships__user=user,
                court__club__memberships__role=ClubMembership.Role.OWNER,
                court__club__memberships__is_active=True,
            ).distinct()
        if user.is_manager():
            return queryset.filter(
                court__club__memberships__user=user,
                court__club__memberships__role=ClubMembership.Role.MANAGER,
                court__club__memberships__is_active=True,
            ).distinct()
        if user.is_staff_member():
            return queryset.filter(user=user, is_active=True)
        return queryset.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
