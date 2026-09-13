from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import ClubScopedViewMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.roles import Role


# Test-only ViewSet for verifying DRF integration without migrating domain ViewSets
class SampleResource:
    def __init__(self, id, club_id, owner_id):
        self.id = id
        self.club_id = club_id
        self.owner_id = owner_id


class SampleResourceViewSet(ClubScopedViewMixin, viewsets.ViewSet):
    permission_classes = [SlotyBasePermission]
    permission_view_name = "BookingViewSet"  # Simulates BookingViewSet matrix mapping

    def list(self, request, club_slug=None):
        return Response({"status": "ok", "role": request.access_context.role})

    def create(self, request, club_slug=None):
        return Response({"status": "created"}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def cancel(self, request, club_slug=None):
        return Response({"status": "cancelled"})

    @action(detail=True, methods=["post"])
    def custom_restricted_action(self, request, pk=None, club_slug=None):
        return Response({"status": "custom"})


# Test-only ViewSet with Object Permission Extension
class SampleObjectRestrictedViewSet(ClubScopedViewMixin, viewsets.ViewSet):
    permission_classes = [SlotyBasePermission]
    permission_view_name = "BookingViewSet"

    def retrieve(self, request, pk=None, club_slug=None):
        obj = SampleResource(id=pk, club_id=1, owner_id=999)
        self.check_object_permissions(request, obj)
        return Response({"status": "retrieved"})

    def check_object_permission(self, request, obj):
        # Dedicated object-level rule: user must be the object owner
        return obj.owner_id == request.user.id


class BasePermissionAndMatrixTestCase(APITestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.club = Club.objects.create(
            name="Al-Ahly Club",
            slug="al-ahly-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.admin_user = User.objects.create_user(
            username="admin-user", password="password", is_platform_admin=True
        )
        self.owner_user = User.objects.create_user(
            username="owner-user", password="password"
        )
        self.manager_user = User.objects.create_user(
            username="manager-user", password="password"
        )
        self.staff_user = User.objects.create_user(
            username="staff-user", password="password"
        )

        ClubMembership.objects.create(
            club=self.club,
            user=self.owner_user,
            role=ClubMembership.Role.OWNER,
            is_active=True,
        )
        ClubMembership.objects.create(
            club=self.club,
            user=self.manager_user,
            role=ClubMembership.Role.MANAGER,
            is_active=True,
        )
        ClubMembership.objects.create(
            club=self.club,
            user=self.staff_user,
            role=ClubMembership.Role.STAFF,
            is_active=True,
        )

    def test_default_deny_rules(self):
        """Matrix must strictly default to deny on unknown role, viewset, or action."""
        self.assertFalse(is_action_allowed("UNKNOWN_ROLE", "BookingViewSet", "list"))
        self.assertFalse(is_action_allowed(Role.OWNER, "NonexistentViewSet", "list"))
        self.assertFalse(
            is_action_allowed(Role.OWNER, "BookingViewSet", "unknown_action")
        )
        self.assertFalse(is_action_allowed(None, "BookingViewSet", "list"))
        self.assertFalse(is_action_allowed(Role.STAFF, None, "list"))
        self.assertFalse(is_action_allowed(Role.STAFF, "BookingViewSet", None))

    def test_verified_matrix_permissions_for_all_four_roles(self):
        # 1. ADMIN has global full access
        self.assertTrue(is_action_allowed(Role.ADMIN, "BookingViewSet", "list"))
        self.assertTrue(is_action_allowed(Role.ADMIN, "CourtViewSet", "create"))
        self.assertTrue(
            is_action_allowed(Role.ADMIN, "SettlementViewSet", "unsettled_summary")
        )
        self.assertTrue(is_action_allowed(Role.ADMIN, "AuditLogViewSet", "list"))

        # 2. OWNER
        self.assertTrue(is_action_allowed(Role.OWNER, "BookingViewSet", "list"))
        self.assertTrue(is_action_allowed(Role.OWNER, "BookingViewSet", "cancel"))
        self.assertTrue(is_action_allowed(Role.OWNER, "CourtViewSet", "create"))
        self.assertTrue(
            is_action_allowed(Role.OWNER, "ClubMembershipViewSet", "create")
        )
        self.assertTrue(is_action_allowed(Role.OWNER, "AuditLogViewSet", "list"))
        self.assertFalse(
            is_action_allowed(Role.OWNER, "ClubViewSet", "create")
        )  # Only Admin creates clubs

        # 3. MANAGER
        self.assertTrue(is_action_allowed(Role.MANAGER, "BookingViewSet", "list"))
        self.assertTrue(is_action_allowed(Role.MANAGER, "BookingViewSet", "complete"))
        self.assertTrue(is_action_allowed(Role.MANAGER, "AuditLogViewSet", "list"))
        self.assertFalse(
            is_action_allowed(Role.MANAGER, "CourtViewSet", "create")
        )  # Cannot create courts
        self.assertFalse(
            is_action_allowed(Role.MANAGER, "ClubMembershipViewSet", "create")
        )  # Cannot onboard members

        # 4. STAFF
        self.assertTrue(is_action_allowed(Role.STAFF, "BookingViewSet", "list"))
        self.assertTrue(is_action_allowed(Role.STAFF, "BookingViewSet", "create"))
        self.assertTrue(is_action_allowed(Role.STAFF, "BookingViewSet", "cancel"))
        self.assertTrue(is_action_allowed(Role.STAFF, "TransactionViewSet", "create"))
        self.assertTrue(is_action_allowed(Role.STAFF, "SettlementViewSet", "preview"))
        self.assertFalse(
            is_action_allowed(Role.STAFF, "AuditLogViewSet", "list")
        )  # Staff denied audit
        self.assertFalse(
            is_action_allowed(Role.STAFF, "SettlementViewSet", "create")
        )  # Staff cannot settle
        self.assertFalse(
            is_action_allowed(Role.STAFF, "CourtViewSet", "create")
        )  # Staff cannot create courts
        self.assertFalse(
            is_action_allowed(Role.STAFF, "ClubMembershipViewSet", "list")
        )  # Staff cannot view members

    def test_drf_viewset_integration_with_scoped_mixin(self):
        view = SampleResourceViewSet.as_view({"get": "list", "post": "create"})

        # Unauthenticated request -> 401
        request = self.factory.get("/api/v1/clubs/al-ahly-club/sample/")
        response = view(request, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Authenticated Staff request -> 200 Allowed
        request = self.factory.get("/api/v1/clubs/al-ahly-club/sample/")
        force_authenticate(request, user=self.staff_user)
        response = view(request, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("role"), Role.STAFF)

    def test_drf_custom_action_resolution(self):
        view = SampleResourceViewSet.as_view({"post": "cancel"})
        request = self.factory.post("/api/v1/clubs/al-ahly-club/sample/cancel/")
        force_authenticate(request, user=self.staff_user)

        response = view(request, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "cancelled")

    def test_drf_unconfigured_custom_action_denied(self):
        view = SampleResourceViewSet.as_view({"post": "custom_restricted_action"})
        request = self.factory.post(
            "/api/v1/clubs/al-ahly-club/sample/1/custom_restricted_action/"
        )
        force_authenticate(request, user=self.staff_user)

        response = view(request, pk=1, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_object_permission_extension_hook(self):
        view = SampleObjectRestrictedViewSet.as_view({"get": "retrieve"})

        # Request user does NOT own object (owner_id=999) -> 403 Forbidden
        # via check_object_permission
        request = self.factory.get("/api/v1/clubs/al-ahly-club/sample/1/")
        force_authenticate(request, user=self.staff_user)  # staff_user.id != 999
        response = view(request, pk=1, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # When request user matches object.owner_id -> 200 OK
        request = self.factory.get("/api/v1/clubs/al-ahly-club/sample/1/")
        dummy_user = User.objects.create_user(
            id=999, username="owner-999", password="password"
        )
        ClubMembership.objects.create(
            club=self.club,
            user=dummy_user,
            role=ClubMembership.Role.STAFF,
            is_active=True,
        )
        force_authenticate(request, user=dummy_user)
        response = view(request, pk=1, club_slug="al-ahly-club")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
