from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from apps.clubs.models import Club
from apps.common.authorization.matrix import ROLE_PERMISSIONS, is_action_allowed
from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.roles import Role
from apps.courts.models import Court
from apps.profiles.models import Profile, StaffCourtAccess, StaffProfile
from tests.profile_factories import ProfileIdentityMixin


class SampleResourceViewSet(ClubScopedViewMixin, viewsets.ViewSet):
    permission_classes = [SlotyBasePermission]
    permission_view_name = "BookingViewSet"

    def list(self, request, club_slug=None):
        return Response({"role": request.access_context.role})

    @action(detail=False, methods=["post"])
    def cancel(self, request, club_slug=None):
        return Response({"status": "cancelled"})

    @action(detail=True, methods=["post"])
    def custom_restricted_action(self, request, pk=None, club_slug=None):
        return Response({"status": "custom"})


class BasePermissionAndMatrixTestCase(ProfileIdentityMixin, APITestCase):

    def setUp(self):
        self.factory = APIRequestFactory()
        self.club = Club.objects.create(
            name="Al-Ahly Club",
            slug="al-ahly-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court = Court.objects.create(
            club=self.club, name="Court", default_price="200.00"
        )
        self.staff = self.create_user(password="password")
        sp = StaffProfile.objects.create(
            user=self.staff, role=Profile.Role.STAFF, club=self.club
        )
        StaffCourtAccess.objects.create(staff_profile=sp, court=self.court)

    def test_default_deny_rules(self):
        self.assertFalse(is_action_allowed("UNKNOWN", "BookingViewSet", "list"))
        self.assertFalse(is_action_allowed(Role.OWNER, "UnknownViewSet", "list"))
        self.assertFalse(is_action_allowed(Role.STAFF, "BookingViewSet", "unknown"))

    def test_matrix_contains_only_supported_roles(self):
        for role in (Role.ADMIN, Role.OWNER, Role.STAFF):
            self.assertTrue(is_action_allowed(role, "BookingViewSet", "list"))
        self.assertTrue(is_action_allowed(Role.ADMIN, "CourtViewSet", "create"))
        self.assertTrue(is_action_allowed(Role.OWNER, "SettlementViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.STAFF, "SettlementViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.STAFF, "AuditLogViewSet", "list"))
        self.assertEqual(set(ROLE_PERMISSIONS), set(Profile.Role.values))

    def test_onboarding_hierarchy_matrix_rules(self):
        # Admin creates Owner; Owner and Staff cannot
        self.assertTrue(is_action_allowed(Role.ADMIN, "OwnerViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.OWNER, "OwnerViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.STAFF, "OwnerViewSet", "create"))

        # Owner creates Staff; Admin and Staff cannot directly create
        self.assertFalse(is_action_allowed(Role.ADMIN, "StaffViewSet", "create"))
        self.assertTrue(is_action_allowed(Role.OWNER, "StaffViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.STAFF, "StaffViewSet", "create"))

    def test_drf_uses_profile_context_and_matrix(self):
        view = SampleResourceViewSet.as_view({"get": "list", "post": "cancel"})
        unauthenticated = self.factory.get("/api/v1/clubs/al-ahly-club/sample/")
        self.assertEqual(
            view(unauthenticated, club_slug=self.club.slug).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        request = self.factory.get("/api/v1/clubs/al-ahly-club/sample/")
        force_authenticate(request, user=self.staff)
        response = view(request, club_slug=self.club.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], Role.STAFF)

    def test_unconfigured_custom_action_is_denied(self):
        view = SampleResourceViewSet.as_view({"post": "custom_restricted_action"})
        request = self.factory.post("/api/v1/clubs/al-ahly-club/sample/1/custom/")
        force_authenticate(request, user=self.staff)
        self.assertEqual(
            view(request, pk=1, club_slug=self.club.slug).status_code,
            status.HTTP_403_FORBIDDEN,
        )
