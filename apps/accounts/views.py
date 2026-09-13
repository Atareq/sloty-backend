from django.db.models import Prefetch, Subquery
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import RetrieveAPIView
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.filters import UserFilter
from apps.accounts.models import User
from apps.accounts.permissions import CanAccessUsers, IsPlatformSuperAdmin
from apps.accounts.serializers import (
    SlotyTokenObtainPairSerializer,
    SyncHeartbeatResponseSerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserMeSerializer,
    UserUpdateSerializer,
)
from apps.clubs.models import ClubMembership


class SlotyTokenObtainPairView(TokenObtainPairView):
    serializer_class = SlotyTokenObtainPairSerializer


@extend_schema(tags=["Accounts"], responses=UserMeSerializer)
class MeAPIView(RetrieveAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = UserMeSerializer

    def get_object(self):
        return (
            User.objects.select_related("created_by")
            .prefetch_related(
                Prefetch(
                    "club_memberships",
                    queryset=ClubMembership.objects.granting_access()
                    .select_related("club", "court")
                    .order_by("club__name", "role", "id"),
                    to_attr="active_memberships_for_me",
                )
            )
            .get(pk=self.request.user.pk)
        )


@extend_schema(tags=["Accounts"], responses=SyncHeartbeatResponseSerializer)
class SyncHeartbeatAPIView(APIView):
    """
    Explicit offline/PWA sync heartbeat.

    ARCHITECTURAL NOTE:
    This is the ONLY mechanism that updates `ClubMembership.last_sync_at`.
    Ordinary authenticated API traffic (Courts, Transactions, Bookings, etc.)
    never updates it as an implicit response side effect. Authorization
    (authentication, role authority, club/resource scope, authorized
    queryset construction) and sync/presence tracking are separate
    concerns; the Authorization Spine (apps/common/authorization/) owns
    only the former. The frontend/PWA is expected to call this endpoint
    periodically (e.g. every ~5 minutes) while active so the backend clock
    remains the single source of truth for "last synced" state.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request, *args, **kwargs):
        now = timezone.now()
        ClubMembership.objects.granting_access().filter(user=request.user).update(
            last_sync_at=now
        )
        return Response(SyncHeartbeatResponseSerializer({"last_sync_at": now}).data)


@extend_schema_view(
    list=extend_schema(tags=["Accounts"], responses=UserListSerializer),
    create=extend_schema(
        tags=["Accounts"],
        request=UserCreateSerializer,
        responses=UserListSerializer,
    ),
    retrieve=extend_schema(tags=["Accounts"], responses=UserListSerializer),
    partial_update=extend_schema(
        tags=["Accounts"],
        request=UserUpdateSerializer,
        responses=UserListSerializer,
    ),
)
class UserViewSet(
    ListModelMixin,
    CreateModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
    GenericViewSet,
):
    queryset = User.objects.select_related("created_by").order_by("id")
    permission_classes = (IsPlatformSuperAdmin,)
    permission_classes = (CanAccessUsers,)
    filter_backends = (DjangoFilterBackend,)
    filterset_class = UserFilter
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()

        user = self.request.user
        if not (user and user.is_authenticated):
            return User.objects.none()

        if user.is_platform_super_admin():
            return User.objects.select_related("created_by").order_by("id")

        owned_club_ids = (
            ClubMembership.objects.granting_access()
            .filter(
                user=user,
                role=ClubMembership.Role.OWNER,
            )
            .values("club_id")
        )

        staff_user_ids = (
            ClubMembership.objects.current()
            .filter(
                club_id__in=Subquery(owned_club_ids),
                role=ClubMembership.Role.STAFF,
            )
            .values("user_id")
        )

        return (
            User.objects.filter(id__in=Subquery(staff_user_ids))
            .select_related("created_by")
            .order_by("id")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in {"partial_update", "update"}:
            return UserUpdateSerializer
        return UserListSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
