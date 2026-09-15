from django.http import Http404
from rest_framework.exceptions import NotAuthenticated
from rest_framework.test import APIRequestFactory, APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.authorization.context import RequestAccessContext
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope
from apps.common.authorization.roles import Role
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile


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
        self.court_a = Court.objects.create(
            club=self.club_a, name="Court A", default_price="200.00"
        )
        self.court_b = Court.objects.create(
            club=self.club_b, name="Court B", default_price="250.00"
        )
        self.owner = self.create_profile_user(
            "owner-user", Profile.Role.OWNER, club=self.club_a
        )
        self.staff = self.create_profile_user(
            "staff-user", Profile.Role.STAFF, court=self.court_a
        )
        self.admin = self.create_profile_user("platform-admin", Profile.Role.ADMIN)
        self.unscoped_user = User.objects.create_user(
            username="unscoped", password="password"
        )

    def create_profile_user(self, username, role, *, club=None, court=None):
        user = User.objects.create_user(username=username, password="password")
        profile = Profile.objects.create(user=user, role=role)
        if role == Profile.Role.ADMIN:
            AdminProfile.objects.create(profile=profile)
        elif role == Profile.Role.OWNER:
            owner_profile = OwnerProfile.objects.create(profile=profile)
            owner_profile.clubs.add(club)
        else:
            StaffProfile.objects.create(profile=profile, court=court)
        return user

    def request_for(self, user, club):
        request = self.factory.get(f"/api/v1/clubs/{club.slug}/bookings/")
        request.user = user
        return request

    def test_unauthenticated_request_raises_not_authenticated(self):
        request = self.factory.get("/api/v1/clubs/club-al-ahly/bookings/")
        request.user = None
        with self.assertRaises(NotAuthenticated):
            resolve_club_scope(request, club_slug=self.club_a.slug)

    def test_owner_resolves_from_owner_profile_clubs(self):
        context = resolve_club_scope(
            self.request_for(self.owner, self.club_a), self.club_a.slug
        )
        self.assertIsInstance(context, RequestAccessContext)
        self.assertEqual(context.role, Role.OWNER)
        self.assertEqual(context.owner_profile.profile.user_id, self.owner.id)
        self.assertIsNone(context.court)

    def test_staff_resolves_from_assigned_court_without_targeting_it(self):
        context = resolve_club_scope(
            self.request_for(self.staff, self.club_a), self.club_a.slug
        )
        self.assertEqual(context.role, Role.STAFF)
        self.assertEqual(context.staff_profile.court_id, self.court_a.id)
        self.assertIsNone(context.court)

    def test_explicit_court_is_resolved_only_inside_url_club(self):
        context = resolve_club_scope(
            self.request_for(self.staff, self.club_a),
            self.club_a.slug,
            court_id=self.court_a.id,
        )
        self.assertEqual(context.court, self.court_a)
        with self.assertRaises(Http404):
            resolve_club_scope(
                self.request_for(self.owner, self.club_a),
                self.club_a.slug,
                court_id=self.court_b.id,
            )

    def test_owner_cannot_access_unowned_club(self):
        with self.assertRaises(SlotyAPIException) as caught:
            resolve_club_scope(
                self.request_for(self.owner, self.club_b), self.club_b.slug
            )
        self.assertEqual(caught.exception.api_code, "CLUB_ACCESS_REVOKED")

    def test_missing_profile_is_denied(self):
        with self.assertRaises(SlotyAPIException) as caught:
            resolve_club_scope(
                self.request_for(self.unscoped_user, self.club_a), self.club_a.slug
            )
        self.assertEqual(caught.exception.api_code, "CLUB_ACCESS_REVOKED")

    def test_admin_has_platform_scope_from_profile_role(self):
        context = resolve_club_scope(
            self.request_for(self.admin, self.club_b), self.club_b.slug
        )
        self.assertTrue(context.is_platform_admin)
        self.assertEqual(context.role, Role.ADMIN)

    def test_context_is_cached_on_request(self):
        request = self.request_for(self.owner, self.club_a)
        context = resolve_club_scope(request, self.club_a.slug)
        with self.assertNumQueries(0):
            self.assertIs(resolve_club_scope(request, self.club_a.slug), context)

    def test_global_scope_uses_admin_profile(self):
        request = self.factory.get("/api/v1/users/")
        request.user = self.admin
        context = resolve_global_scope(request)
        self.assertTrue(context.is_platform_admin)
        self.assertIsNone(context.club)
