from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from apps.accounts.models import User
from apps.clubs.models import Club, ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.scopes import ResourceScope
from apps.courts.models import Court


class ScopedMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClubMembership
        fields = ("id", "club", "court", "user", "role")


class ScopedMembershipModelViewSet(
    SlotyScopedResourceMixin,
    viewsets.ModelViewSet,
):
    # Deliberately unscoped: the mixin must reconstruct a secured queryset.
    queryset = ClubMembership.objects.all()
    serializer_class = ScopedMembershipSerializer
    permission_classes = (IsAuthenticated,)
    authorization_scope = ResourceScope.COURT

    def filter_scoped_queryset(self, queryset):
        return queryset.order_by("id")


class ScopedMembershipViewSet(SlotyScopedResourceMixin, viewsets.ViewSet):
    queryset = ClubMembership.objects.all()
    permission_classes = (IsAuthenticated,)
    authorization_scope = ResourceScope.CLUB

    def list(self, request, club_slug=None):
        queryset = self.get_queryset().order_by("id")
        return Response(
            {
                "ids": list(queryset.values_list("id", flat=True)),
                "context_club": self.get_access_context().club.slug,
            }
        )


class AuthorizationSpineV2TestCase(APITestCase):
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "court"},
            "collector": {"path": "user"},
        },
        "default_scope": "court",
        "select_related": ("club",),
        "prefetch_related": ("user__groups",),
    }
    court_authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "self"},
        },
        "default_scope": "court",
        "select_related": ("club",),
        "prefetch_related": (),
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._missing_config = object()
        cls._original_config = getattr(
            ClubMembership,
            "authorization_config",
            cls._missing_config,
        )
        ClubMembership.authorization_config = cls.authorization_config
        cls._original_court_config = getattr(
            Court,
            "authorization_config",
            cls._missing_config,
        )
        Court.authorization_config = cls.court_authorization_config

    @classmethod
    def tearDownClass(cls):
        if cls._original_config is cls._missing_config:
            del ClubMembership.authorization_config
        else:
            ClubMembership.authorization_config = cls._original_config
        if cls._original_court_config is cls._missing_config:
            del Court.authorization_config
        else:
            Court.authorization_config = cls._original_court_config
        super().tearDownClass()

    def setUp(self):
        self.factory = APIRequestFactory()
        self.club_a = Club.objects.create(
            name="Al Mostaqbal Club",
            slug="al-mostaqbal-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.club_b = Club.objects.create(
            name="Al Rowad Club",
            slug="al-rowad-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court_a1 = Court.objects.create(
            club=self.club_a,
            name="Main Court",
            default_price="250.00",
        )
        self.court_a2 = Court.objects.create(
            club=self.club_a,
            name="Academy Court",
            default_price="200.00",
        )
        self.court_b1 = Court.objects.create(
            club=self.club_b,
            name="North Court",
            default_price="300.00",
        )

        self.owner = User.objects.create_user(username="owner", password="password")
        self.staff_a1 = User.objects.create_user(
            username="staff-a1", password="password"
        )
        self.staff_a2 = User.objects.create_user(
            username="staff-a2", password="password"
        )
        self.staff_b1 = User.objects.create_user(
            username="staff-b1", password="password"
        )

        self.owner_membership = ClubMembership.objects.create(
            club=self.club_a,
            user=self.owner,
            role=ClubMembership.Role.OWNER,
        )
        self.staff_a1_membership = ClubMembership.objects.create(
            club=self.club_a,
            court=self.court_a1,
            user=self.staff_a1,
            role=ClubMembership.Role.STAFF,
        )
        self.staff_a2_membership = ClubMembership.objects.create(
            club=self.club_a,
            court=self.court_a2,
            user=self.staff_a2,
            role=ClubMembership.Role.STAFF,
        )
        self.staff_b1_membership = ClubMembership.objects.create(
            club=self.club_b,
            court=self.court_b1,
            user=self.staff_b1,
            role=ClubMembership.Role.STAFF,
        )

    def context_for(self, user, *, club=None, court=None):
        selected_club = club or self.club_a
        request = self.factory.get(f"/api/v1/clubs/{selected_club.slug}/resources/")
        request.user = user
        return resolve_club_scope(
            request,
            club_slug=selected_club.slug,
            court_id=court.pk if court else None,
        )

    def test_model_scope_configuration_loading(self):
        config = load_authorization_config(ClubMembership)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertEqual(config.scopes["collector"].path, "user")
        self.assertEqual(config.select_related, ("club",))
        self.assertEqual(config.prefetch_related, ("user__groups",))

    def test_missing_model_configuration_fails_closed(self):
        original_config = ClubMembership.authorization_config
        del ClubMembership.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner),
                    ClubMembership,
                    scope=ResourceScope.CLUB,
                )
        finally:
            ClubMembership.authorization_config = original_config

    def test_club_scope_filters_out_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner),
            ClubMembership,
            scope=ResourceScope.CLUB,
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)),
            {
                self.owner_membership.id,
                self.staff_a1_membership.id,
                self.staff_a2_membership.id,
            },
        )

    def test_court_scope_filters_staff_to_assigned_court(self):
        context = self.context_for(self.staff_a1)
        queryset = scoped_queryset(
            context,
            ClubMembership,
            scope=ResourceScope.COURT,
        )

        self.assertEqual(context.membership.court_id, self.court_a1.id)
        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)),
            {self.staff_a1_membership.id},
        )

    def test_explicit_court_scope_filters_owner_to_requested_court(self):
        queryset = scoped_queryset(
            self.context_for(self.owner, court=self.court_a2),
            ClubMembership,
            scope=ResourceScope.COURT,
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)),
            {self.staff_a2_membership.id},
        )

    def test_court_model_can_scope_itself_without_model_specific_code(self):
        queryset = scoped_queryset(
            self.context_for(self.staff_a1),
            Court,
            scope=ResourceScope.COURT,
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)),
            {self.court_a1.id},
        )

    def test_future_scope_uses_context_fact_after_club_boundary(self):
        context = self.context_for(self.owner)
        future_context = SimpleNamespace(
            **context.__dict__,
            collector=self.staff_a2,
        )

        queryset = scoped_queryset(
            future_context,
            ClubMembership,
            scope="collector",
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)),
            {self.staff_a2_membership.id},
        )

    def test_none_scope_and_invalid_context_never_return_unscoped_rows(self):
        context = self.context_for(self.owner)
        forged_context = SimpleNamespace(
            **{**context.__dict__, "membership": None},
        )

        self.assertFalse(
            scoped_queryset(
                context,
                ClubMembership,
                scope=ResourceScope.NONE,
            ).exists()
        )
        self.assertFalse(
            scoped_queryset(
                forged_context,
                ClubMembership,
                scope=ResourceScope.CLUB,
            ).exists()
        )

    def test_mandatory_and_viewset_relation_loading_are_applied(self):
        queryset = scoped_queryset(
            self.context_for(self.owner),
            ClubMembership,
            scope=ResourceScope.CLUB,
            extra_select_related=("user",),
            extra_prefetch_related=("club__courts",),
        )

        self.assertEqual(set(queryset.query.select_related), {"club", "user"})
        self.assertEqual(
            queryset._prefetch_related_lookups,
            ("user__groups", "club__courts"),
        )

    def test_unscoped_modelviewset_queryset_cannot_bypass_mixin(self):
        view = ScopedMembershipModelViewSet.as_view({"get": "list"})
        request = self.factory.get(f"/api/v1/clubs/{self.club_a.slug}/memberships/")
        force_authenticate(request, user=self.owner)

        response = view(request, club_slug=self.club_a.slug)
        rows = response.data.get("results", response.data)

        self.assertEqual(response.status_code, 200)
        self.assertSetEqual(
            {row["id"] for row in rows},
            {self.staff_a1_membership.id, self.staff_a2_membership.id},
        )
        self.assertNotIn(self.staff_b1_membership.id, {row["id"] for row in rows})

    def test_mixin_composes_with_plain_drf_viewset(self):
        view = ScopedMembershipViewSet.as_view({"get": "list"})
        request = self.factory.get(f"/api/v1/clubs/{self.club_a.slug}/memberships/")
        force_authenticate(request, user=self.owner)

        response = view(request, club_slug=self.club_a.slug)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["context_club"], self.club_a.slug)
        self.assertSetEqual(
            set(response.data["ids"]),
            {
                self.owner_membership.id,
                self.staff_a1_membership.id,
                self.staff_a2_membership.id,
            },
        )

    def test_stock_modelviewset_is_untouched_and_mixin_owns_scoping(self):
        self.assertNotIn(SlotyScopedResourceMixin, viewsets.ModelViewSet.__mro__)
        self.assertNotIn("get_queryset", viewsets.ModelViewSet.__dict__)
        self.assertIs(
            ScopedMembershipModelViewSet.get_queryset,
            SlotyScopedResourceMixin.get_queryset,
        )
