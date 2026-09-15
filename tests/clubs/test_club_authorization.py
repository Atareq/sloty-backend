"""
Regression and security tests for the Clubs Authorization Spine v2 migration.

Validates:
1. Club.authorization_config and ClubMembership.authorization_config satisfy the
   Authorization Spine v2 contract (apps/common/authorization/contracts.py) and
   scope correctly through the generic scoped_queryset() resolver.
2. ClubMembershipViewSet and ClubUserListViewSet rely on SlotyScopedResourceMixin
   and SlotyBasePermission.
3. Cross-club isolation raises CLUB_ACCESS_REVOKED (HTTP 403).
4. Direct ID attacks return 404 (no existence leak) when accessing another club's
   membership via the URL club scope.
5. Role authority matrix:
   - Admin: Full access.
   - Owner: Can manage club and memberships; cannot edit/delete another Owner.
   - Manager: Denied membership management (/memberships/ -> 403); can list club
     users (/users/), with Owners and inactive staff filtered out.
   - Staff: Denied membership management and club user list (403).
6. Query parameter filters cannot broaden authorization.
7. Migrated Clubs endpoints do NOT update ClubMembership.last_sync_at implicitly.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.clubs.authorization import (
    apply_club_users_scoping,
    can_create_membership,
    can_list_club_users,
    can_manage_club,
    can_manage_membership_object,
    can_manage_memberships,
)
from apps.clubs.models import Club, ClubMembership
from apps.clubs.views import ClubMembershipViewSet, ClubUserListViewSet
from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.mixins import SlotyScopedResourceMixin
from apps.common.authorization.permissions import SlotyBasePermission
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.scopes import ResourceScope
from tests.clubs.test_club_api import ClubAPITestCase


class ClubAuthorizationConfigTests(ClubAPITestCase):
    """
    Test model-level authorization_config contracts and scoped_queryset resolution.
    """

    def setUp(self):
        self.platform_admin = self.create_platform_admin("authz-admin")
        self.owner = self.create_user("authz-owner")
        self.other_owner = self.create_user("authz-other-owner")
        self.staff = self.create_user("authz-staff")
        self.club = self.create_club("Authz Club", slug="authz-club")
        self.other_club = self.create_club("Authz Other Club", slug="authz-other-club")
        self.court = self.create_court(self.club, "Authz Court")

        self.owner_membership = self.create_membership(
            self.owner, self.club, ClubMembership.Role.OWNER
        )
        self.staff_membership = self.create_membership(
            self.staff, self.club, ClubMembership.Role.STAFF, court=self.court
        )
        self.other_owner_membership = self.create_membership(
            self.other_owner, self.other_club, ClubMembership.Role.OWNER
        )

    def context_for(self, user, club=None):
        target_club = club or self.club
        request = APIRequestFactory().get(
            f"/api/v1/clubs/{target_club.slug}/memberships/"
        )
        request.user = user
        return resolve_club_scope(request, club_slug=target_club.slug)

    def test_club_model_authorization_config(self):
        config = load_authorization_config(Club)
        self.assertEqual(config.default_scope, ResourceScope.CLUB.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "self")
        self.assertIn("created_by", config.select_related)

    def test_club_membership_model_authorization_config(self):
        config = load_authorization_config(ClubMembership)
        self.assertEqual(config.default_scope, ResourceScope.CLUB.value)
        self.assertEqual(config.scopes[ResourceScope.CLUB.value].path, "club")
        self.assertIn("club", config.select_related)
        self.assertIn("user", config.select_related)
        self.assertIn("court", config.select_related)

    def test_scoped_queryset_enforces_club_boundary(self):
        ctx = self.context_for(self.owner)
        qs = scoped_queryset(ctx, ClubMembership, scope=ResourceScope.CLUB)
        membership_ids = set(qs.values_list("id", flat=True))

        self.assertIn(self.owner_membership.id, membership_ids)
        self.assertIn(self.staff_membership.id, membership_ids)
        self.assertNotIn(self.other_owner_membership.id, membership_ids)

    def test_viewset_spine_contracts(self):
        self.assertTrue(issubclass(ClubMembershipViewSet, SlotyScopedResourceMixin))
        self.assertTrue(issubclass(ClubUserListViewSet, SlotyScopedResourceMixin))
        self.assertIn(SlotyBasePermission, ClubMembershipViewSet.permission_classes)
        self.assertIn(SlotyBasePermission, ClubUserListViewSet.permission_classes)


class ClubDomainAuthorizationUnitTests(ClubAPITestCase):
    """Direct unit tests for apps.clubs.authorization functions."""

    def setUp(self):
        self.admin_user = self.create_platform_admin("unit-admin")
        self.owner_user = self.create_user("unit-owner")
        self.other_owner_user = self.create_user("unit-other-owner")
        self.manager_user = self.create_user("unit-manager")
        self.staff_user = self.create_user("unit-staff")
        self.club = self.create_club("Unit Club", slug="unit-club")
        self.other_club = self.create_club("Unit Other Club", slug="unit-other-club")
        self.court = self.create_court(self.club, "Unit Court")
        self.other_court = self.create_court(self.other_club, "Other Court")

        self.owner_m = self.create_membership(
            self.owner_user, self.club, ClubMembership.Role.OWNER
        )
        self.other_owner_m = self.create_membership(
            self.other_owner_user, self.club, ClubMembership.Role.OWNER
        )
        self.manager_m = self.create_membership(
            self.manager_user, self.club, ClubMembership.Role.MANAGER
        )
        self.staff_m = self.create_membership(
            self.staff_user, self.club, ClubMembership.Role.STAFF, court=self.court
        )

    def context_for(self, user):
        request = APIRequestFactory().get(
            f"/api/v1/clubs/{self.club.slug}/memberships/"
        )
        request.user = user
        return resolve_club_scope(request, club_slug=self.club.slug)

    def test_can_manage_memberships(self):
        self.assertTrue(can_manage_memberships(self.context_for(self.admin_user)))
        self.assertTrue(can_manage_memberships(self.context_for(self.owner_user)))
        self.assertFalse(can_manage_memberships(self.context_for(self.manager_user)))
        self.assertFalse(can_manage_memberships(self.context_for(self.staff_user)))

    def test_can_create_membership_role_restrictions(self):
        admin_ctx = self.context_for(self.admin_user)
        owner_ctx = self.context_for(self.owner_user)
        manager_ctx = self.context_for(self.manager_user)
        staff_ctx = self.context_for(self.staff_user)

        # Admin can create all roles
        self.assertTrue(can_create_membership(admin_ctx, ClubMembership.Role.OWNER))
        self.assertTrue(can_create_membership(admin_ctx, ClubMembership.Role.MANAGER))
        self.assertTrue(
            can_create_membership(
                admin_ctx, ClubMembership.Role.STAFF, court=self.court
            )
        )

        # Owner cannot create OWNER; can create MANAGER and STAFF
        self.assertFalse(can_create_membership(owner_ctx, ClubMembership.Role.OWNER))
        self.assertTrue(can_create_membership(owner_ctx, ClubMembership.Role.MANAGER))
        self.assertTrue(
            can_create_membership(
                owner_ctx, ClubMembership.Role.STAFF, court=self.court
            )
        )

        # Owner cannot attach a court from a different club
        self.assertFalse(
            can_create_membership(
                owner_ctx, ClubMembership.Role.STAFF, court=self.other_court
            )
        )

        # Manager and Staff cannot create memberships
        self.assertFalse(
            can_create_membership(manager_ctx, ClubMembership.Role.MANAGER)
        )
        self.assertFalse(
            can_create_membership(
                staff_ctx, ClubMembership.Role.STAFF, court=self.court
            )
        )

    def test_can_manage_membership_object_rules(self):
        admin_ctx = self.context_for(self.admin_user)
        owner_ctx = self.context_for(self.owner_user)

        # Admin can mutate any membership, including Owners
        self.assertTrue(
            can_manage_membership_object(admin_ctx, self.owner_m, "partial_update")
        )
        self.assertTrue(
            can_manage_membership_object(admin_ctx, self.owner_m, "destroy")
        )

        # Owner can retrieve another Owner
        self.assertTrue(
            can_manage_membership_object(owner_ctx, self.other_owner_m, "retrieve")
        )

        # Owner CANNOT update or destroy another Owner
        self.assertFalse(
            can_manage_membership_object(
                owner_ctx, self.other_owner_m, "partial_update"
            )
        )
        self.assertFalse(
            can_manage_membership_object(owner_ctx, self.other_owner_m, "destroy")
        )

        # Owner can update or destroy Manager and Staff
        self.assertTrue(
            can_manage_membership_object(owner_ctx, self.manager_m, "partial_update")
        )
        self.assertTrue(
            can_manage_membership_object(owner_ctx, self.staff_m, "destroy")
        )

    def test_can_list_club_users(self):
        self.assertTrue(can_list_club_users(self.context_for(self.admin_user)))
        self.assertTrue(can_list_club_users(self.context_for(self.owner_user)))
        self.assertTrue(can_list_club_users(self.context_for(self.manager_user)))
        self.assertFalse(can_list_club_users(self.context_for(self.staff_user)))

    def test_apply_club_users_scoping_manager_visibility(self):
        inactive_staff = self.create_user("inactive-staff")
        self.create_membership(
            inactive_staff,
            self.club,
            ClubMembership.Role.STAFF,
            court=self.court,
            is_active=False,
        )

        base_qs = ClubMembership.objects.filter(club=self.club)

        # Manager sees only active Manager and Staff (excludes Owner and inactive staff)
        manager_scoped = apply_club_users_scoping(
            self.context_for(self.manager_user), base_qs
        )
        manager_user_ids = set(manager_scoped.values_list("user_id", flat=True))
        self.assertIn(self.manager_user.id, manager_user_ids)
        self.assertIn(self.staff_user.id, manager_user_ids)
        self.assertNotIn(self.owner_user.id, manager_user_ids)
        self.assertNotIn(self.other_owner_user.id, manager_user_ids)
        self.assertNotIn(inactive_staff.id, manager_user_ids)

        # Owner sees all non-deleted memberships
        owner_scoped = apply_club_users_scoping(
            self.context_for(self.owner_user), base_qs
        )
        owner_user_ids = set(owner_scoped.values_list("user_id", flat=True))
        self.assertIn(self.owner_user.id, owner_user_ids)
        self.assertIn(self.manager_user.id, owner_user_ids)
        self.assertIn(self.staff_user.id, owner_user_ids)
        self.assertIn(inactive_staff.id, owner_user_ids)

    def test_can_manage_club_global_operations(self):
        # Create: SuperAdmin only
        self.assertTrue(can_manage_club(self.admin_user, self.club, "create"))
        self.assertFalse(can_manage_club(self.owner_user, self.club, "create"))

        # Update: SuperAdmin and Owner of that club
        self.assertTrue(can_manage_club(self.admin_user, self.club, "update"))
        self.assertTrue(can_manage_club(self.owner_user, self.club, "update"))
        self.assertFalse(can_manage_club(self.manager_user, self.club, "update"))
        self.assertFalse(can_manage_club(self.staff_user, self.club, "update"))

        # Retrieve: Any active member of that club
        self.assertTrue(can_manage_club(self.owner_user, self.club, "retrieve"))
        self.assertTrue(can_manage_club(self.manager_user, self.club, "retrieve"))
        self.assertTrue(can_manage_club(self.staff_user, self.club, "retrieve"))
        self.assertFalse(
            can_manage_club(self.create_user("unrelated"), self.club, "retrieve")
        )


class ClubSecurityIntegrationAPITests(ClubAPITestCase):
    """
    End-to-end API integration tests verifying security boundaries and attack scenarios.
    """

    def setUp(self):
        self.admin = self.create_platform_admin("sec-admin")
        self.owner_a = self.create_user("sec-owner-a")
        self.owner_b = self.create_user("sec-owner-b")
        self.manager_a = self.create_user("sec-manager-a")
        self.staff_a = self.create_user("sec-staff-a")
        self.inactive_staff_a = self.create_user("sec-inactive-staff-a")

        self.club_a = self.create_club("Sec Club A", slug="sec-club-a")
        self.club_b = self.create_club("Sec Club B", slug="sec-club-b")

        self.court_a = self.create_court(self.club_a, "Sec Court A")
        self.court_b = self.create_court(self.club_b, "Sec Court B")

        self.mem_owner_a = self.create_membership(
            self.owner_a, self.club_a, ClubMembership.Role.OWNER
        )
        self.mem_manager_a = self.create_membership(
            self.manager_a, self.club_a, ClubMembership.Role.MANAGER
        )
        self.mem_staff_a = self.create_membership(
            self.staff_a, self.club_a, ClubMembership.Role.STAFF, court=self.court_a
        )
        self.mem_inactive_a = self.create_membership(
            self.inactive_staff_a,
            self.club_a,
            ClubMembership.Role.STAFF,
            court=self.court_a,
            is_active=False,
        )

        self.mem_owner_b = self.create_membership(
            self.owner_b, self.club_b, ClubMembership.Role.OWNER
        )

    # 1. Cross-club isolation
    def test_cross_club_isolation_denies_revoked_access(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(self.membership_list_url(self.club_b))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")

    # 2. Direct membership ID attack across clubs
    # (must return 404, not 403 existence leak)
    def test_direct_membership_id_attack_returns_404(self):
        self.client.force_authenticate(user=self.owner_a)
        # Attempt to access Club B's membership ID via Club A's URL
        response = self.client.get(
            reverse(
                "club-membership-detail",
                kwargs={"club_slug": self.club_a.slug, "pk": self.mem_owner_b.pk},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # 3. Direct club ID access
    def test_direct_club_id_access_unauthorized_returns_404(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            reverse("club-detail", kwargs={"pk": self.club_b.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # 4. Owner cannot edit or soft-delete another Owner membership
    def test_owner_cannot_edit_or_delete_owner_membership(self):
        second_owner = self.create_user("second-owner-a")
        mem_second_owner = self.create_membership(
            second_owner, self.club_a, ClubMembership.Role.OWNER
        )

        self.client.force_authenticate(user=self.owner_a)

        # PATCH is_active on second owner -> 403
        patch_response = self.client.patch(
            self.membership_detail_url(self.club_a, mem_second_owner),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_403_FORBIDDEN)

        # DELETE second owner -> 403
        delete_response = self.client.delete(
            self.membership_detail_url(self.club_a, mem_second_owner)
        )
        self.assertEqual(delete_response.status_code, status.HTTP_403_FORBIDDEN)

    # 5. Manager cannot manage memberships
    def test_manager_cannot_manage_memberships(self):
        self.client.force_authenticate(user=self.manager_a)

        # List memberships -> 403 (matrix denied)
        list_response = self.client.get(self.membership_list_url(self.club_a))
        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)

        # Create membership -> 403
        create_response = self.client.post(
            self.membership_list_url(self.club_a),
            {
                "user": self.create_user("new-user").id,
                "role": ClubMembership.Role.STAFF,
                "court": self.court_a.id,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

    # 6. Staff cannot manage memberships or list club users
    def test_staff_cannot_manage_memberships_or_list_users(self):
        self.client.force_authenticate(user=self.staff_a)

        # Memberships -> 403
        self.assertEqual(
            self.client.get(self.membership_list_url(self.club_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Club users -> 403
        self.assertEqual(
            self.client.get(self.club_user_list_url(self.club_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # 7. Manager user list scoping & filter cannot broaden
    def test_manager_user_list_scoping_and_filter_broaden_protection(self):
        self.client.force_authenticate(user=self.manager_a)

        # Normal list: sees Manager and active Staff only
        response = self.client.get(self.club_user_list_url(self.club_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(self.manager_a.id, user_ids)
        self.assertIn(self.staff_a.id, user_ids)
        self.assertNotIn(self.owner_a.id, user_ids)
        self.assertNotIn(self.inactive_staff_a.id, user_ids)

        # Attack: Manager attempts to filter by role=OWNER -> empty
        owner_filter_resp = self.client.get(
            self.club_user_list_url(self.club_a), {"role": ClubMembership.Role.OWNER}
        )
        self.assertEqual(owner_filter_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(owner_filter_resp.data["results"]), 0)

        # Attack: Manager attempts to filter by is_active=false -> empty
        inactive_filter_resp = self.client.get(
            self.club_user_list_url(self.club_a), {"is_active": "false"}
        )
        self.assertEqual(inactive_filter_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(inactive_filter_resp.data["results"]), 0)

    # 8. Platform Admin authority
    def test_platform_admin_full_authority(self):
        self.client.force_authenticate(user=self.admin)

        # Admin can view all clubs
        clubs_resp = self.client.get(reverse("club-list"))
        self.assertEqual(clubs_resp.status_code, status.HTTP_200_OK)
        club_ids = {c["id"] for c in clubs_resp.data["results"]}
        self.assertIn(self.club_a.id, club_ids)
        self.assertIn(self.club_b.id, club_ids)

        # Admin can list any club's memberships
        mem_resp = self.client.get(self.membership_list_url(self.club_a))
        self.assertEqual(mem_resp.status_code, status.HTTP_200_OK)

        # Admin can update any club's memberships including Owners
        patch_resp = self.client.patch(
            self.membership_detail_url(self.club_a, self.mem_owner_a),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)

    # 9. No implicit last_sync_at update on migrated endpoints
    def test_migrated_club_endpoints_do_not_update_last_sync(self):
        self.client.force_authenticate(user=self.owner_a)

        self.assertIsNone(self.mem_owner_a.last_sync_at)

        # Calling membership list
        self.client.get(self.membership_list_url(self.club_a))
        self.mem_owner_a.refresh_from_db()
        self.assertIsNone(self.mem_owner_a.last_sync_at)

        # Calling club user list
        self.client.get(self.club_user_list_url(self.club_a))
        self.mem_owner_a.refresh_from_db()
        self.assertIsNone(self.mem_owner_a.last_sync_at)
