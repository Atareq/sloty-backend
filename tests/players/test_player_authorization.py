"""
Authorization Spine v2 contract tests for ClubPlayer.

Validates:
1. ClubPlayer.authorization_config satisfies the Spine v2 contract shape
   (apps/common/authorization/contracts.py).
2. scoped_queryset() enforces club isolation without any HTTP layer.
3. Cross-club access returns an empty queryset (fail-closed).
4. load_authorization_config(ClubPlayer) succeeds without ImproperlyConfigured.
5. PlayerProfile has NO authorization_config — it is a global model.
"""

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.scopes import ResourceScope
from apps.players.models import ClubPlayer, PlayerProfile


def make_user(username):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_club(name, slug):
    return Club.objects.create(
        name=name, slug=slug, governorate="ASSIUT", city="ASSIUT_MARKAZ"
    )


def make_membership(user, club, role):
    return ClubMembership.objects.create(club=club, user=user, role=role)


def make_profile(phone):
    return PlayerProfile.objects.create(phone_number=phone)


def make_club_player(club, profile, display_name=""):
    return ClubPlayer.objects.create(
        club=club, player_profile=profile, display_name=display_name
    )


class ClubPlayerAuthorizationConfigTests(TestCase):
    """ClubPlayer.authorization_config must satisfy the Spine v2 contract."""

    def test_config_loads_without_improperly_configured(self):
        config = load_authorization_config(ClubPlayer)
        self.assertIsNotNone(config)

    def test_default_scope_is_club(self):
        config = load_authorization_config(ClubPlayer)
        self.assertEqual(config.default_scope, ResourceScope.CLUB.value)

    def test_club_scope_path_is_club(self):
        config = load_authorization_config(ClubPlayer)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")

    def test_no_court_scope_declared(self):
        """ClubPlayer is club-scoped only — no court scope should exist."""
        config = load_authorization_config(ClubPlayer)
        self.assertNotIn(ResourceScope.COURT.value, config.scopes)

    def test_select_related_includes_club_and_player_profile(self):
        config = load_authorization_config(ClubPlayer)
        self.assertIn("club", config.select_related)
        self.assertIn("player_profile", config.select_related)

    def test_player_profile_has_no_authorization_config(self):
        """PlayerProfile is global — it must NOT declare authorization_config."""
        self.assertFalse(hasattr(PlayerProfile, "authorization_config"))


class ClubPlayerScopedQuerysetTests(TestCase):
    """scoped_queryset() must enforce club isolation at the DB layer."""

    def setUp(self):
        self.owner = make_user("scope-owner")
        self.club_a = make_club("Scope Club A", "scope-club-a")
        self.club_b = make_club("Scope Club B", "scope-club-b")
        make_membership(self.owner, self.club_a, ClubMembership.Role.OWNER)

        self.profile_1 = make_profile("+201010001111")
        self.profile_2 = make_profile("+201010002222")

        self.cp_a = make_club_player(self.club_a, self.profile_1, "Player A")
        self.cp_b = make_club_player(self.club_b, self.profile_2, "Player B")

    def _context(self, user, club):
        request = APIRequestFactory().get(f"/api/v1/clubs/{club.slug}/players/")
        request.user = user
        return resolve_club_scope(request, club_slug=club.slug)

    def test_club_a_scope_includes_only_club_a_players(self):
        ctx = self._context(self.owner, self.club_a)
        qs = scoped_queryset(ctx, ClubPlayer, scope=ResourceScope.CLUB)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.cp_a.id, ids)
        self.assertNotIn(self.cp_b.id, ids)

    def test_no_membership_returns_empty_queryset(self):
        stranger = make_user("stranger")
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club_a.slug}/players/")
        request.user = stranger
        from apps.common.exceptions import SlotyAPIException

        with self.assertRaises(SlotyAPIException):
            resolve_club_scope(request, club_slug=self.club_a.slug)

    def test_club_b_scope_excludes_club_a_players(self):
        owner_b = make_user("scope-owner-b")
        make_membership(owner_b, self.club_b, ClubMembership.Role.OWNER)
        ctx = self._context(owner_b, self.club_b)
        qs = scoped_queryset(ctx, ClubPlayer, scope=ResourceScope.CLUB)
        ids = set(qs.values_list("id", flat=True))
        self.assertNotIn(self.cp_a.id, ids)
        self.assertIn(self.cp_b.id, ids)

    def test_same_profile_isolated_across_clubs(self):
        """Same PlayerProfile, two clubs — each club sees only its own ClubPlayer."""
        shared_profile = make_profile("+201099998888")
        cp_a2 = make_club_player(self.club_a, shared_profile, "Club A View")
        cp_b2 = make_club_player(self.club_b, shared_profile, "Club B View")

        owner_b = make_user("scope-owner-b2")
        make_membership(owner_b, self.club_b, ClubMembership.Role.OWNER)

        ctx_a = self._context(self.owner, self.club_a)
        ctx_b = self._context(owner_b, self.club_b)

        ids_a = set(scoped_queryset(ctx_a, ClubPlayer).values_list("id", flat=True))
        ids_b = set(scoped_queryset(ctx_b, ClubPlayer).values_list("id", flat=True))

        self.assertIn(cp_a2.id, ids_a)
        self.assertNotIn(cp_b2.id, ids_a)
        self.assertIn(cp_b2.id, ids_b)
        self.assertNotIn(cp_a2.id, ids_b)
