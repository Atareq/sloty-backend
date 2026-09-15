from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.scopes import ResourceScope
from apps.courts.models import Court
from apps.profiles.models import OwnerProfile, Profile, StaffProfile


class CourtSerializer(serializers.ModelSerializer):
    class Meta:
        model = Court
        fields = ("id", "name")


class ScopedCourtViewSet(SlotyScopedResourceMixin, viewsets.ModelViewSet):
    queryset = Court.objects.all()
    serializer_class = CourtSerializer
    permission_classes = (IsAuthenticated,)
    authorization_scope = ResourceScope.COURT


class AuthorizationSpineV2TestCase(APITestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.club_a = Club.objects.create(
            name="Scope Club A",
            slug="scope-club-a",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.club_b = Club.objects.create(
            name="Scope Club B",
            slug="scope-club-b",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court_a1 = Court.objects.create(
            club=self.club_a, name="Court A1", default_price="200.00"
        )
        self.court_a2 = Court.objects.create(
            club=self.club_a, name="Court A2", default_price="200.00"
        )
        self.court_b = Court.objects.create(
            club=self.club_b, name="Court B", default_price="200.00"
        )
        self.owner = User.objects.create_user(
            username="scope-owner", password="password"
        )
        owner_profile = Profile.objects.create(user=self.owner, role=Profile.Role.OWNER)
        OwnerProfile.objects.create(profile=owner_profile).clubs.add(self.club_a)
        self.staff = User.objects.create_user(
            username="scope-staff", password="password"
        )
        staff_profile = Profile.objects.create(user=self.staff, role=Profile.Role.STAFF)
        StaffProfile.objects.create(profile=staff_profile, court=self.court_a1)

    def context_for(self, user, club=None, court=None):
        club = club or self.club_a
        request = self.factory.get(f"/api/v1/clubs/{club.slug}/courts/")
        request.user = user
        return resolve_club_scope(
            request, club.slug, court_id=court.pk if court else None
        )

    def test_court_model_has_valid_scope_configuration(self):
        config = load_authorization_config(Court)
        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "self")

    def test_staff_court_scope_is_profile_assigned_court_only(self):
        queryset = scoped_queryset(self.context_for(self.staff), Court)
        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)), {self.court_a1.id}
        )

    def test_owner_can_see_club_courts_and_target_one(self):
        club_queryset = scoped_queryset(self.context_for(self.owner), Court)
        self.assertSetEqual(
            set(club_queryset.values_list("id", flat=True)),
            {self.court_a1.id, self.court_a2.id},
        )
        targeted_queryset = scoped_queryset(
            self.context_for(self.owner, court=self.court_a2), Court
        )
        self.assertSetEqual(
            set(targeted_queryset.values_list("id", flat=True)), {self.court_a2.id}
        )

    def test_invalid_context_fails_closed(self):
        context = self.context_for(self.owner)
        forged = SimpleNamespace(**{**context.__dict__, "profile": None})
        self.assertFalse(scoped_queryset(forged, Court).exists())

    def test_missing_configuration_raises_instead_of_returning_unscoped_rows(self):
        original = Court.authorization_config
        del Court.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(self.context_for(self.owner), Court)
        finally:
            Court.authorization_config = original

    def test_mixin_reconstructs_scoped_queryset_from_unscoped_declaration(self):
        view = ScopedCourtViewSet.as_view({"get": "list"})
        request = self.factory.get(f"/api/v1/clubs/{self.club_a.slug}/courts/")
        force_authenticate(request, user=self.staff)
        response = view(request, club_slug=self.club_a.slug)
        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual([row["id"] for row in rows], [self.court_a1.id])
