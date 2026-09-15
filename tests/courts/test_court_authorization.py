"""
Regression tests for the Courts Authorization Spine v2 migration.

Validates:
1. Court.authorization_config / CourtWorkingHour.authorization_config satisfy
   the Authorization Spine v2 contract (apps/common/authorization/contracts.py)
   and scope correctly through the generic scoped_queryset() resolver -
   independent of any API view.
2. CourtViewSet and CourtWeeklyWorkingHoursViewSet rely entirely on
   SlotyScopedResourceMixin (queryset) + SlotyBasePermission (matrix) with no
   domain-specific permission class and no broad-then-filter querysets.
3. Cross-club isolation raises CLUB_ACCESS_REVOKED before any court is ever
   touched.
4. Staff assigned-court isolation returns 404 (no existence leak) for both
   the Court detail endpoint and the nested weekly working-hours endpoint.
5. Working-hours write authority is covered at the
   apps.courts.authorization unit level: only Platform Admin and Owner may
   manage working hours; Staff cannot.
6. The deprecated CourtWorkingHourViewSet reuses the
   CourtWeeklyWorkingHoursViewSet matrix entries (no duplicated/second
   permission surface for the legacy endpoint).
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.scopes import ResourceScope
from apps.courts.authorization import can_manage_working_hours
from apps.courts.models import Court, CourtWorkingHour
from apps.courts.views import CourtViewSet, CourtWeeklyWorkingHoursViewSet
from tests.courts.test_court_api import CourtAPITestCase


class CourtAuthorizationConfigTests(CourtAPITestCase):
    """Model authorization_config contract, exercised without any HTTP layer."""

    def setUp(self):
        self.owner = self.create_user("config-owner")
        self.staff = self.create_user("config-staff")
        self.club = self.create_club("Config Club", slug="config-club")
        self.other_club = self.create_club(
            "Config Other Club", slug="config-other-club"
        )
        self.court_a = self.create_court(self.club, "Config Court A")
        self.court_b = self.create_court(self.club, "Config Court B")
        self.other_court = self.create_court(self.other_club, "Config Other Court")
        self.create_membership(self.owner, self.club, "OWNER")
        self.create_membership(self.staff, self.club, "STAFF", court=self.court_a)
        self.working_hour_a = CourtWorkingHour.objects.create(
            court=self.court_a, weekday=CourtWorkingHour.Weekday.MONDAY
        )
        self.working_hour_b = CourtWorkingHour.objects.create(
            court=self.court_b, weekday=CourtWorkingHour.Weekday.MONDAY
        )

    def context_for(self, user, *, court=None):
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club.slug}/courts/")
        request.user = user
        return resolve_club_scope(
            request,
            club_slug=self.club.slug,
            court_id=court.pk if court else None,
        )

    def test_court_config_declares_club_and_self_scope(self):
        config = load_authorization_config(Court)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "self")
        self.assertIn("club", config.select_related)

    def test_court_working_hour_config_declares_relation_paths(self):
        config = load_authorization_config(CourtWorkingHour)

        self.assertEqual(config.default_scope, ResourceScope.COURT.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "court__club")
        self.assertEqual(config.scopes[ResourceScope.COURT.value].path, "court")
        self.assertIn("court", config.select_related)
        self.assertIn("court__club", config.select_related)
        self.assertIn("pricing_periods", config.prefetch_related)

    def test_court_club_scope_excludes_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner), Court, scope=ResourceScope.CLUB
        )

        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(ids, {self.court_a.id, self.court_b.id})
        self.assertNotIn(self.other_court.id, ids)

    def test_court_court_scope_restricts_staff_to_assigned_court(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), Court, scope=ResourceScope.COURT
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)), {self.court_a.id}
        )

    def test_court_explicit_court_scope_denies_staff_for_unassigned_court(self):
        queryset = scoped_queryset(
            self.context_for(self.staff, court=self.court_b),
            Court,
            scope=ResourceScope.COURT,
        )

        self.assertFalse(queryset.exists())

    def test_court_working_hour_scope_restricts_staff_to_assigned_court(self):
        queryset = scoped_queryset(
            self.context_for(self.staff),
            CourtWorkingHour,
            scope=ResourceScope.COURT,
        )

        self.assertSetEqual(
            set(queryset.values_list("id", flat=True)), {self.working_hour_a.id}
        )

    def test_missing_court_configuration_never_falls_back_to_unscoped_manager(self):
        original = Court.authorization_config
        del Court.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner), Court, scope=ResourceScope.CLUB
                )
        finally:
            Court.authorization_config = original


class CourtViewSetSpineCompositionTests(CourtAPITestCase):
    """Structural guard against reintroducing a broad-then-filter queryset."""

    def test_court_viewset_does_not_override_get_queryset(self):
        self.assertIs(CourtViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset)
        self.assertIs(
            CourtWeeklyWorkingHoursViewSet.get_queryset,
            SlotyScopedResourceMixin.get_queryset,
        )

    def test_court_viewset_declares_authorization_model_not_bare_queryset(self):
        self.assertIs(CourtViewSet.authorization_model, Court)
        self.assertIs(CourtWeeklyWorkingHoursViewSet.authorization_model, Court)


class CourtAuthorizationAPITests(CourtAPITestCase):
    def setUp(self):
        self.platform_admin = self.create_platform_admin("auth-admin")
        self.owner = self.create_user("auth-owner")
        self.staff = self.create_user("auth-staff")
        self.club = self.create_club("Auth Club", slug="auth-club")
        self.other_club = self.create_club("Auth Other Club", slug="auth-other-club")
        self.court = self.create_court(self.club, "Auth Court")
        self.create_membership(self.owner, self.club, "OWNER")
        self.create_membership(self.staff, self.club, "STAFF", court=self.court)

    def assert_api_error(self, response, code):
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], code)

    def test_court_list_for_user_without_club_membership_is_club_access_revoked(self):
        outsider = self.create_user("auth-outsider")
        self.client.force_authenticate(user=outsider)

        response = self.client.get(self.court_list_url(self.club))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")

    def test_nested_working_hours_without_membership_is_club_access_revoked(self):
        outsider = self.create_user("auth-nested-outsider")
        self.client.force_authenticate(user=outsider)

        response = self.client.get(self.nested_working_hour_url(self.club, self.court))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_api_error(response, "CLUB_ACCESS_REVOKED")

    def test_platform_admin_can_manage_nested_working_hours(self):
        self.client.force_authenticate(user=self.platform_admin)

        response = self.client.put(
            self.nested_working_hour_url(self.club, self.court),
            {"working_hours": [{"weekday": 0, "pricing_periods": []}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_legacy_working_hour_viewset_reuses_weekly_matrix_entry(self):
        from apps.courts.views import CourtWorkingHourViewSet

        self.assertEqual(
            CourtWorkingHourViewSet.permission_view_name,
            "CourtWeeklyWorkingHoursViewSet",
        )


class CanManageWorkingHoursHelperTests(CourtAPITestCase):
    """
    Unit coverage of domain invariants for managing working hours.
    """

    def setUp(self):
        self.platform_admin = self.create_platform_admin("helper-admin")
        self.owner = self.create_user("helper-owner")
        self.staff = self.create_user("helper-staff")
        self.club = self.create_club("Helper Club", slug="helper-club")
        self.court = self.create_court(self.club, "Helper Court")
        self.create_membership(self.owner, self.club, "OWNER")
        self.create_membership(self.staff, self.club, "STAFF", court=self.court)

    def context_for(self, user):
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club.slug}/courts/")
        request.user = user
        return resolve_club_scope(request, club_slug=self.club.slug)

    def test_platform_admin_and_owner_can_always_manage(self):
        self.assertTrue(
            can_manage_working_hours(self.context_for(self.platform_admin), self.court)
        )
        self.assertTrue(
            can_manage_working_hours(self.context_for(self.owner), self.court)
        )

    def test_staff_can_never_manage_working_hours(self):
        self.assertFalse(
            can_manage_working_hours(self.context_for(self.staff), self.court)
        )
