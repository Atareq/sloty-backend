from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import NotAuthenticated
from rest_framework.test import APIRequestFactory, APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court


class ScopeResolverTestCase(APITestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.club_a = Club.objects.create(
            name="Club Al-Ahly",
            slug="club-al-ahly",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.club_b = Club.objects.create(
            name="Club Zamalek",
            slug="club-zamalek",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court_a1 = Court.objects.create(
            club=self.club_a,
            name="Court 1",
            default_price="200.00",
        )
        self.court_b1 = Court.objects.create(
            club=self.club_b,
            name="Court B1",
            default_price="250.00",
        )

        # Users
        self.owner_user = User.objects.create_user(
            username="owner-user", password="password"
        )
        self.manager_user = User.objects.create_user(
            username="manager-user", password="password"
        )
        self.staff_user = User.objects.create_user(
            username="staff-user", password="password"
        )
        self.platform_admin_user = User.objects.create_user(
            username="platform-admin",
            password="password",
            is_platform_admin=True,
        )
        self.unrelated_user = User.objects.create_user(
            username="unrelated-user", password="password"
        )

        # Memberships in Club A
        ClubMembership.objects.create(
            club=self.club_a,
            user=self.owner_user,
            role=ClubMembership.Role.OWNER,
            is_active=True,
        )
        ClubMembership.objects.create(
            club=self.club_a,
            user=self.manager_user,
            role=ClubMembership.Role.MANAGER,
            is_active=True,
        )
        ClubMembership.objects.create(
            club=self.club_a,
            user=self.staff_user,
            role=ClubMembership.Role.STAFF,
            court=self.court_a1,
            is_active=True,
        )

    def test_unauthenticated_request_raises_not_authenticated(self):
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = None

        with self.assertRaises(NotAuthenticated):
            resolve_club_scope(request, club_slug="club-al-ahly")

    def test_authenticated_owner_resolves_correct_context(self):
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.owner_user

        context = resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertIsInstance(context, RequestAccessContext)
        self.assertEqual(context.user, self.owner_user)
        self.assertEqual(context.club, self.club_a)
        self.assertEqual(context.role, Role.OWNER)
        self.assertEqual(context.profile_type, Role.OWNER)
        self.assertIsNotNone(context.membership)
        self.assertEqual(context.membership.role, ClubMembership.Role.OWNER)
        self.assertFalse(context.is_platform_admin)
        self.assertIsNone(context.court)

    def test_authenticated_manager_resolves_correct_context(self):
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.manager_user

        context = resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertEqual(context.role, Role.MANAGER)
        self.assertEqual(context.profile_type, Role.MANAGER)
        self.assertIsNone(context.court)

    def test_authenticated_staff_does_not_preload_court_on_club_wide_request(self):
        """
        ARCHITECTURAL INVARIANT:
        Do NOT preload Staff court assignments on club-wide requests.
        context.court must remain None unless explicitly targeted via URL.
        """
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.staff_user

        context = resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertEqual(context.role, Role.STAFF)
        self.assertEqual(context.profile_type, Role.STAFF)
        self.assertIsNone(context.court)

    def test_explicit_court_id_resolves_court(self):
        request = self.factory.get(
            f"/api/v1/clubs/club-al-ahly/courts/{self.court_a1.id}/"
        )
        request.user = self.staff_user

        context = resolve_club_scope(
            request,
            club_slug="club-al-ahly",
            court_id=self.court_a1.id,
        )

        self.assertIsNotNone(context.court)
        self.assertEqual(context.court, self.court_a1)

    def test_explicit_court_from_different_club_raises_404(self):
        request = self.factory.get(
            f"/api/v1/clubs/club-al-ahly/courts/{self.court_b1.id}/"
        )
        request.user = self.owner_user

        with self.assertRaises(Http404):
            resolve_club_scope(
                request,
                club_slug="club-al-ahly",
                court_id=self.court_b1.id,
            )

    def test_nonexistent_club_slug_raises_404(self):
        request = self.factory.get("/api/v1/clubs/nonexistent-club/bookings/")
        request.user = self.owner_user

        with self.assertRaises(Http404):
            resolve_club_scope(request, club_slug="nonexistent-club")

    def test_user_without_club_membership_raises_club_access_revoked(self):
        """
        User with membership in Club A accessing Club B must be denied
        with structured error code CLUB_ACCESS_REVOKED (HTTP 403).
        """
        request = self.factory.get("/api/v1/clubs/club-zamalek/bookings/")
        request.user = self.owner_user  # Only member of club_a

        with self.assertRaises(SlotyAPIException) as cm:
            resolve_club_scope(request, club_slug="club-zamalek")

        exc = cm.exception
        self.assertEqual(exc.status_code, 403)
        self.assertEqual(exc.api_code, "CLUB_ACCESS_REVOKED")
        self.assertEqual(exc.details.get("club_slug"), "club-zamalek")

    def test_deactivated_membership_raises_club_access_revoked(self):
        ClubMembership.objects.filter(club=self.club_a, user=self.staff_user).update(
            is_active=False
        )

        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.staff_user

        with self.assertRaises(SlotyAPIException) as cm:
            resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(cm.exception.api_code, "CLUB_ACCESS_REVOKED")

    def test_soft_deleted_membership_raises_club_access_revoked(self):
        ClubMembership.objects.filter(club=self.club_a, user=self.staff_user).update(
            deleted_at=timezone.now()
        )

        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.staff_user

        with self.assertRaises(SlotyAPIException) as cm:
            resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertEqual(cm.exception.status_code, 403)
        self.assertEqual(cm.exception.api_code, "CLUB_ACCESS_REVOKED")

    def test_platform_admin_resolves_admin_role_without_club_membership(self):
        request = self.factory.get("/api/v1/clubs/club-zamalek/bookings/")
        request.user = self.platform_admin_user

        context = resolve_club_scope(request, club_slug="club-zamalek")

        self.assertEqual(context.role, Role.ADMIN)
        self.assertEqual(context.profile_type, Role.ADMIN)
        self.assertTrue(context.is_platform_admin)
        self.assertEqual(context.club, self.club_b)
        self.assertIsNone(context.membership)

    def test_context_is_cached_on_request(self):
        """
        Repeated calls to resolve_club_scope within the same request lifecycle
        must use the cached context and perform zero additional DB queries.
        """
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = self.owner_user

        # First resolution hits the database
        context1 = resolve_club_scope(request, club_slug="club-al-ahly")
        self.assertIsNotNone(context1)

        # Second resolution should be purely from cache
        with self.assertNumQueries(0):
            context2 = resolve_club_scope(request, club_slug="club-al-ahly")

        self.assertIs(context1, context2)

    def test_resolve_global_scope(self):
        request = self.factory.get("/api/v1/users/")
        request.user = self.platform_admin_user

        context = resolve_global_scope(request)
        self.assertTrue(context.is_platform_admin)
        self.assertEqual(context.role, Role.ADMIN)
        self.assertIsNone(context.club)

        # Cached call should produce 0 queries
        with self.assertNumQueries(0):
            context2 = resolve_global_scope(request)
        self.assertIs(context, context2)
