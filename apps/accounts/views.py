from django.db.models import Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
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
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.common.throttling import (
    AnonymousScopedRateThrottle,
    LoginIPRateThrottle,
    LoginUsernameRateThrottle,
)

from apps.accounts.filters import UserFilter
from apps.accounts.models import User
from apps.accounts.permissions import CanAccessUsers
from apps.accounts.serializers import (
    PasswordChangeSerializer,
    SlotyTokenObtainPairSerializer,
    SlotyTokenRefreshSerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserMeSerializer,
    UserUpdateSerializer,
)
from apps.profiles.models import Profile


class SlotyTokenObtainPairView(TokenObtainPairView):
    serializer_class = SlotyTokenObtainPairSerializer
    # Two buckets: one per source IP, one per submitted username. IP alone does
    # not stop a distributed attack on a single account; username alone does not
    # stop one host spraying many accounts.
    throttle_classes = (LoginIPRateThrottle, LoginUsernameRateThrottle)


class SlotyTokenRefreshView(TokenRefreshView):
    serializer_class = SlotyTokenRefreshSerializer
    # Looser than login: legitimate clients refresh on a timer, and a valid
    # refresh token is already proof of a prior successful authentication.
    throttle_classes = (AnonymousScopedRateThrottle,)
    throttle_scope = "auth_refresh"


@extend_schema(
    tags=["Accounts"], request=PasswordChangeSerializer, responses={204: None}
)
class PasswordChangeAPIView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, *args, **kwargs):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Accounts"], responses=UserMeSerializer)
class MeAPIView(RetrieveAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = UserMeSerializer

    def get_object(self):
        return (
            User.objects.select_related("created_by")
            .select_related("profile")
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
    queryset = (
        User.objects.select_related("created_by")
        .select_related("profile")
        .order_by("id")
    )
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
            return (
                User.objects.select_related("created_by")
                .select_related("profile")
                .order_by("id")
            )
        profile = getattr(user, "profile", None)
        if not profile or not profile.is_enabled or profile.role != Profile.Role.OWNER:
            return User.objects.none()
        owned_club_ids = profile.owner_profile.clubs.values("id")
        staff_user_ids = Profile.objects.filter(
            role=Profile.Role.STAFF,
            is_enabled=True,
            staff_profile__club_id__in=owned_club_ids,
        ).values("user_id")

        return (
            User.objects.filter(id__in=Subquery(staff_user_ids))
            .select_related("created_by")
            .select_related("profile")
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
