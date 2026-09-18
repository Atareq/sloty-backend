"""Soft-deleted rows must vanish from authorized querysets.

apps/common/authorization builds every scoped queryset from _default_manager.
Because the alive-only manager is declared first on SoftDeleteModel, a soft
delete removes the row from every authorized read without any domain app having
to filter for it. That is a deliberate coupling, so it is pinned here: if the
manager order ever changed, deleted courts would silently reappear in staff
querysets and the failure would be invisible.
"""

from rest_framework.test import APIRequestFactory, APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope
from apps.courts.models import Court
from apps.profiles.models import OwnerProfile, Profile


class SoftDeletedScopingTests(APITestCase):

    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        self.club = Club.objects.create(
            name="Soft Delete Club",
            slug="soft-delete-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.kept_court = Court.objects.create(
            club=self.club, name="Kept Court", default_price="200.00"
        )
        self.removed_court = Court.objects.create(
            club=self.club, name="Removed Court", default_price="200.00"
        )
        self.owner = User.objects.create_user(
            username="soft-delete-owner", password="password"
        )
        owner_profile = OwnerProfile.objects.create(
            user=self.owner, role=Profile.Role.OWNER
        )
        owner_profile.clubs.add(self.club)

    def context(self):
        request = self.factory.get(f"/api/v1/clubs/{self.club.slug}/courts/")
        request.user = self.owner
        return resolve_club_scope(request, self.club.slug)

    def test_soft_deleted_court_leaves_the_scoped_queryset(self):
        before = set(scoped_queryset(self.context(), Court).values_list("id", flat=True))
        self.assertEqual(before, {self.kept_court.id, self.removed_court.id})

        self.removed_court.delete(deleted_by=self.owner)

        after = set(scoped_queryset(self.context(), Court).values_list("id", flat=True))
        self.assertEqual(after, {self.kept_court.id})

    def test_restoring_returns_the_row_to_the_scoped_queryset(self):
        self.removed_court.delete()
        self.removed_court.restore()

        scoped = set(scoped_queryset(self.context(), Court).values_list("id", flat=True))
        self.assertIn(self.removed_court.id, scoped)

    def test_deleted_row_is_still_reachable_for_audit_via_all_objects(self):
        self.removed_court.delete(deleted_by=self.owner)

        self.assertTrue(Court.all_objects.filter(pk=self.removed_court.pk).exists())
        self.assertEqual(
            Court.all_objects.get(pk=self.removed_court.pk).deleted_by, self.owner
        )
