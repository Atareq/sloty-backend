"""
Regression tests for Audit Authorization Spine Migration (Sprint 9).

Validates:
1. AuditLog.authorization_config is club-only (never court, never collector).
2. AuditLogViewSet composes SlotyScopedResourceMixin + SlotyBasePermission.
3. Matrix: Admin / Owner / Manager list+retrieve; Staff denied.
4. Club isolation (cross-club 403 / out-of-club ID 404).
5. Audit is not court-scoped: Owner/Manager see all club courts plus
   null-court membership events. Staff remain 403, not court-filtered.
6. Audit is not actor-scoped or collector-scoped.
7. Search / filters / pagination cannot broaden the authorized queryset.
8. No custom actions or exports exist.
9. Historical audit rows are not rewritten by authorization reads.
10. apps/audit/authorization.py is not introduced.
"""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.audit.models import AuditLog
from apps.audit.views import AuditLogViewSet
from apps.clubs.models import ClubMembership
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.matrix import is_action_allowed
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope
from tests.audit.test_audit_api import AuditAPITestCase

AUDIT_ALLOWED_ACTIONS = ("list", "retrieve")
REPO_ROOT = Path(__file__).resolve().parents[2]


class AuditAuthorizationConfigTests(AuditAPITestCase):
    def setUp(self):
        self.owner = self.create_user("audit-config-owner")
        self.staff = self.create_user("audit-config-staff")
        self.club = self.create_club("Audit Config Club", slug="audit-config-club")
        self.other_club = self.create_club(
            "Audit Config Other Club", slug="audit-config-other"
        )
        self.court_a = self.create_court(self.club, "Audit Config Court A")
        self.court_b = self.create_court(self.club, "Audit Config Court B")
        self.other_court = self.create_court(
            self.other_club, "Audit Config Other Court"
        )
        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.court_a_log = self.create_audit_log(
            self.club, court=self.court_a, actor=self.owner, entity_id=1
        )
        self.court_b_log = self.create_audit_log(
            self.club, court=self.court_b, actor=self.staff, entity_id=2
        )
        self.membership_log = self.create_audit_log(
            self.club,
            court=None,
            actor=self.owner,
            action=AuditLog.Action.MEMBERSHIP_DELETED,
            entity_type="ClubMembership",
            entity_id=99,
        )
        self.other_log = self.create_audit_log(
            self.other_club, court=self.other_court, actor=self.owner, entity_id=3
        )

    def context_for(self, user):
        request = APIRequestFactory().get(f"/api/v1/clubs/{self.club.slug}/audit-logs/")
        request.user = user
        return resolve_club_scope(request, club_slug=self.club.slug)

    def test_audit_config_declares_club_scope_only(self):
        config = load_authorization_config(AuditLog)

        self.assertEqual(config.default_scope, ResourceScope.CLUB.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertNotIn(ResourceScope.COURT.value, config.scopes)
        self.assertEqual(config.select_related, ("club", "court", "actor"))

    def test_audit_club_scope_excludes_other_clubs(self):
        queryset = scoped_queryset(
            self.context_for(self.owner), AuditLog, scope=ResourceScope.CLUB
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertSetEqual(
            ids, {self.court_a_log.id, self.court_b_log.id, self.membership_log.id}
        )
        self.assertNotIn(self.other_log.id, ids)

    def test_spine_club_scope_is_not_court_restricted(self):
        queryset = scoped_queryset(
            self.context_for(self.staff), AuditLog, scope=ResourceScope.CLUB
        )
        ids = set(queryset.values_list("id", flat=True))
        self.assertIn(self.court_a_log.id, ids)
        self.assertIn(self.court_b_log.id, ids)
        self.assertIn(self.membership_log.id, ids)

    def test_missing_audit_configuration_never_falls_back(self):
        original = AuditLog.authorization_config
        del AuditLog.authorization_config
        try:
            with self.assertRaises(ImproperlyConfigured):
                scoped_queryset(
                    self.context_for(self.owner),
                    AuditLog,
                    scope=ResourceScope.CLUB,
                )
        finally:
            AuditLog.authorization_config = original


class AuditViewSetSpineCompositionTests(AuditAPITestCase):
    def test_audit_viewset_does_not_override_get_queryset(self):
        self.assertIs(
            AuditLogViewSet.get_queryset, SlotyScopedResourceMixin.get_queryset
        )

    def test_audit_viewset_declares_club_scope_and_spine_permission(self):
        self.assertIs(AuditLogViewSet.authorization_model, AuditLog)
        self.assertEqual(AuditLogViewSet.authorization_scope, ResourceScope.CLUB)
        self.assertEqual(AuditLogViewSet.permission_classes, (SlotyBasePermission,))

    def test_matrix_matches_approved_audit_actions(self):
        for role in (Role.ADMIN, Role.OWNER, Role.MANAGER):
            for action in AUDIT_ALLOWED_ACTIONS:
                with self.subTest(role=role, action=action):
                    self.assertTrue(is_action_allowed(role, "AuditLogViewSet", action))
        for action in AUDIT_ALLOWED_ACTIONS:
            self.assertFalse(is_action_allowed(Role.STAFF, "AuditLogViewSet", action))
        self.assertFalse(is_action_allowed(Role.OWNER, "AuditLogViewSet", "create"))
        self.assertFalse(is_action_allowed(Role.OWNER, "AuditLogViewSet", "destroy"))
        self.assertFalse(is_action_allowed(Role.OWNER, "AuditLogViewSet", "export"))

    def test_audit_views_do_not_use_legacy_access_mixin(self):
        view_source = (REPO_ROOT / "apps" / "audit" / "views.py").read_text()
        self.assertNotIn("ClubScopedAccessMixin", view_source)
        self.assertNotIn("ClubAccessContext", view_source)
        self.assertNotIn("CanViewClubAuditLogs", view_source)
        self.assertFalse((REPO_ROOT / "apps" / "audit" / "authorization.py").exists())
        self.assertFalse((REPO_ROOT / "apps" / "audit" / "permissions.py").exists())


class AuditAuthorizationSpineTests(AuditAPITestCase):
    def setUp(self):
        self.club = self.create_club("Spine Audit Club", "spine-audit")
        self.other_club = self.create_club("Other Audit Club", "other-audit")

        self.court_a = self.create_court(self.club, "Court A")
        self.court_b = self.create_court(self.club, "Court B")
        self.other_court = self.create_court(self.other_club, "Other Club Court")

        self.platform_admin = self.create_platform_admin("spine-audit-admin")
        self.owner = self.create_user("spine-audit-owner")
        self.manager = self.create_user("spine-audit-manager")
        self.staff = self.create_user("spine-audit-staff")
        self.other_club_owner = self.create_user("spine-audit-other-owner")

        self.create_membership(self.owner, self.club, ClubMembership.Role.OWNER)
        self.create_membership(self.manager, self.club, ClubMembership.Role.MANAGER)
        self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.create_membership(
            self.other_club_owner, self.other_club, ClubMembership.Role.OWNER
        )

        self.court_a_log = self.create_audit_log(
            self.club,
            court=self.court_a,
            actor=self.staff,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=11,
            after_data={"customer_phone": "+201012345678"},
        )
        self.court_b_log = self.create_audit_log(
            self.club,
            court=self.court_b,
            actor=self.owner,
            action=AuditLog.Action.TRANSACTION_CREATED,
            entity_type="Transaction",
            entity_id=12,
        )
        self.settlement_log = self.create_audit_log(
            self.club,
            court=None,
            actor=self.manager,
            action=AuditLog.Action.SETTLEMENT_CREATED,
            entity_type="Settlement",
            entity_id=13,
        )
        self.membership_log = self.create_audit_log(
            self.club,
            court=None,
            actor=self.owner,
            action=AuditLog.Action.MEMBERSHIP_DELETED,
            entity_type="ClubMembership",
            entity_id=14,
        )
        self.other_log = self.create_audit_log(
            self.other_club,
            court=self.other_court,
            actor=self.other_club_owner,
            action=AuditLog.Action.BOOKING_CREATED,
            entity_type="Booking",
            entity_id=15,
            after_data={"customer_phone": "+201099999999"},
        )
        self.other_booking = self.create_booking(
            self.other_court,
            customer_name="Other Club Customer",
            customer_phone="+201099999999",
        )
        self.club_booking = self.create_booking(
            self.court_a,
            customer_name="Club A Customer",
            customer_phone="+201012345678",
        )
        self.club_booking_log = self.create_audit_log(
            self.club,
            court=self.court_a,
            actor=self.owner,
            action=AuditLog.Action.BOOKING_UPDATED,
            entity_type="Booking",
            entity_id=self.club_booking.id,
        )
        self.other_booking_log = self.create_audit_log(
            self.other_club,
            court=self.other_court,
            actor=self.other_club_owner,
            action=AuditLog.Action.BOOKING_UPDATED,
            entity_type="Booking",
            entity_id=self.other_booking.id,
        )

    def club_ids(self):
        return {
            self.court_a_log.id,
            self.court_b_log.id,
            self.settlement_log.id,
            self.membership_log.id,
            self.club_booking_log.id,
        }

    def test_platform_admin_owner_and_manager_see_all_club_events(self):
        expected = self.club_ids()
        for user in (self.platform_admin, self.owner, self.manager):
            self.client.force_authenticate(user=user)
            response = self.client.get(self.audit_list_url(self.club))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertSetEqual(self.list_ids(response), expected)
            self.assertNotIn(self.other_log.id, self.list_ids(response))

    def test_staff_are_forbidden_including_own_actions(self):
        self.client.force_authenticate(user=self.staff)
        list_response = self.client.get(self.audit_list_url(self.club))
        own_detail = self.client.get(self.audit_detail_url(self.club, self.court_a_log))
        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(own_detail.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_club_member_cannot_list_or_retrieve(self):
        self.client.force_authenticate(user=self.other_club_owner)
        list_response = self.client.get(self.audit_list_url(self.club))
        detail_on_own_club = self.client.get(
            self.audit_detail_url(self.other_club, self.court_a_log)
        )
        detail_on_foreign_club = self.client.get(
            self.audit_detail_url(self.club, self.court_a_log)
        )
        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(detail_on_own_club.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(detail_on_foreign_club.status_code, status.HTTP_403_FORBIDDEN)

    def test_out_of_club_id_is_not_found(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.audit_detail_url(self.club, self.other_log))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_court_filter_cannot_escape_club_scope(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            self.audit_list_url(self.club),
            {"court": self.other_court.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), set())
        self.assertNotIn(self.other_log.id, self.list_ids(response))

    def test_actor_filter_cannot_escape_club_scope(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            self.audit_list_url(self.club),
            {"actor": self.other_club_owner.id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(response), set())

    def test_owner_sees_events_across_courts_including_null_court(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.audit_list_url(self.club))
        ids = self.list_ids(response)
        self.assertIn(self.court_a_log.id, ids)
        self.assertIn(self.court_b_log.id, ids)
        self.assertIn(self.settlement_log.id, ids)
        self.assertIn(self.membership_log.id, ids)

    def test_search_cannot_discover_other_club_events(self):
        self.client.force_authenticate(user=self.owner)
        other_phone = self.client.get(
            self.audit_list_url(self.club),
            {"search": "01099999999"},
        )
        own_phone = self.client.get(
            self.audit_list_url(self.club),
            {"search": "01012345678"},
        )
        self.assertEqual(other_phone.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(other_phone), set())
        self.assertNotIn(self.other_booking_log.id, self.list_ids(other_phone))
        self.assertEqual(own_phone.status_code, status.HTTP_200_OK)
        self.assertSetEqual(self.list_ids(own_phone), {self.club_booking_log.id})

    def test_staff_search_is_still_forbidden(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(
            self.audit_list_url(self.club),
            {"search": "01012345678"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_pagination_cannot_surface_unauthorized_rows(self):
        extra_other = [
            self.create_audit_log(
                self.other_club,
                court=self.other_court,
                actor=self.other_club_owner,
                entity_id=200 + index,
            )
            for index in range(25)
        ]
        extra_own = [
            self.create_audit_log(
                self.club,
                court=self.court_b,
                actor=self.owner,
                entity_id=300 + index,
            )
            for index in range(16)
        ]
        self.client.force_authenticate(user=self.owner)
        page_one = self.client.get(self.audit_list_url(self.club), {"page": 1})
        page_two = self.client.get(self.audit_list_url(self.club), {"page": 2})
        self.assertEqual(page_one.status_code, status.HTTP_200_OK)
        self.assertEqual(page_two.status_code, status.HTTP_200_OK)
        other_ids = {self.other_log.id, self.other_booking_log.id} | {
            log.id for log in extra_other
        }
        self.assertTrue(self.list_ids(page_one).isdisjoint(other_ids))
        self.assertTrue(self.list_ids(page_two).isdisjoint(other_ids))
        self.assertEqual(page_one.data["count"], 5 + len(extra_own))
        self.assertEqual(len(page_one.data["results"]), 20)
        self.assertEqual(len(page_two.data["results"]), 1)

    def test_authorization_reads_do_not_rewrite_historical_rows(self):
        original = {
            "action": self.court_a_log.action,
            "entity_type": self.court_a_log.entity_type,
            "entity_id": self.court_a_log.entity_id,
            "actor_id": self.court_a_log.actor_id,
            "court_id": self.court_a_log.court_id,
            "club_id": self.court_a_log.club_id,
            "before_data": self.court_a_log.before_data,
            "after_data": self.court_a_log.after_data,
            "metadata": self.court_a_log.metadata,
            "created": self.court_a_log.created,
        }
        self.client.force_authenticate(user=self.owner)
        list_response = self.client.get(self.audit_list_url(self.club))
        detail_response = self.client.get(
            self.audit_detail_url(self.club, self.court_a_log)
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.court_a_log.refresh_from_db()
        self.assertEqual(self.court_a_log.action, original["action"])
        self.assertEqual(self.court_a_log.entity_type, original["entity_type"])
        self.assertEqual(self.court_a_log.entity_id, original["entity_id"])
        self.assertEqual(self.court_a_log.actor_id, original["actor_id"])
        self.assertEqual(self.court_a_log.court_id, original["court_id"])
        self.assertEqual(self.court_a_log.club_id, original["club_id"])
        self.assertEqual(self.court_a_log.before_data, original["before_data"])
        self.assertEqual(self.court_a_log.after_data, original["after_data"])
        self.assertEqual(self.court_a_log.metadata, original["metadata"])
        self.assertEqual(self.court_a_log.created, original["created"])

    def test_audit_list_does_not_update_last_sync_at(self):
        membership = ClubMembership.objects.get(user=self.owner, club=self.club)
        self.assertIsNone(membership.last_sync_at)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.audit_list_url(self.club))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership.refresh_from_db()
        self.assertIsNone(membership.last_sync_at)
