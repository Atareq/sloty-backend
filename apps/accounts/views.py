from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import RetrieveAPIView
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.models import User
from apps.accounts.permissions import IsPlatformSuperAdmin
from apps.accounts.serializers import (
    SlotyTokenObtainPairSerializer,
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
                    queryset=ClubMembership.objects.filter(is_active=True)
                    .select_related("club", "court")
                    .order_by("club__name", "role", "id"),
                    to_attr="active_memberships_for_me",
                )
            )
            .get(pk=self.request.user.pk)
        )


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
    http_method_names = ("get", "post", "patch", "head", "options")

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in {"partial_update", "update"}:
            return UserUpdateSerializer
        return UserListSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
