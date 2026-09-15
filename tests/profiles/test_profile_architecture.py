from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIRequestFactory, APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.authorization.resolver import resolve_club_scope
from apps.common.authorization.roles import Role
from apps.courts.models import Court
from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile


class ProfileArchitectureTests(APITestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.club = Club.objects.create(
            name="Profile Club",
            slug="profile-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.other_club = Club.objects.create(
            name="Other Profile Club",
            slug="other-profile-club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        self.court = Court.objects.create(
            club=self.club, name="Profile Court", default_price="100.00"
        )

    def request_for(self, user, club):
        request = self.factory.get(f"/api/v1/clubs/{club.slug}/courts/")
        request.user = user
        return request

    def test_owner_scope_comes_from_owner_profile_clubs(self):
        user = User.objects.create_user(username="profile-owner", password="pass")
        profile = Profile.objects.create(user=user, role=Profile.Role.OWNER)
        owner_profile = OwnerProfile.objects.create(profile=profile)
        owner_profile.clubs.add(self.club)

        context = resolve_club_scope(self.request_for(user, self.club), self.club.slug)

        self.assertEqual(context.role, Role.OWNER)
        self.assertEqual(context.owner_profile, owner_profile)

    def test_staff_scope_comes_from_staff_profile_court(self):
        user = User.objects.create_user(username="profile-staff", password="pass")
        profile = Profile.objects.create(user=user, role=Profile.Role.STAFF)
        staff_profile = StaffProfile.objects.create(profile=profile, court=self.court)

        context = resolve_club_scope(self.request_for(user, self.club), self.club.slug)

        self.assertEqual(context.role, Role.STAFF)
        self.assertEqual(context.staff_profile, staff_profile)

    def test_admin_scope_comes_from_profile_role(self):
        user = User.objects.create_user(username="profile-admin", password="pass")
        profile = Profile.objects.create(user=user, role=Profile.Role.ADMIN)
        AdminProfile.objects.create(profile=profile)

        context = resolve_club_scope(
            self.request_for(user, self.other_club), self.other_club.slug
        )

        self.assertTrue(context.is_platform_admin)
        self.assertEqual(context.role, Role.ADMIN)

    def test_extension_role_mismatch_is_rejected(self):
        user = User.objects.create_user(username="invalid-profile", password="pass")
        profile = Profile.objects.create(user=user, role=Profile.Role.STAFF)

        with self.assertRaises(ValidationError):
            OwnerProfile.objects.create(profile=profile)

    def test_albaladya_seed_uses_profiles_and_deactivates_legacy_manager(self):
        legacy_manager = User.objects.create_user(
            username="manager", password="pass", is_active=True
        )

        call_command("seed_demo_data", scenario="albaladya-test", verbosity=0)

        self.assertFalse(User.objects.get(pk=legacy_manager.pk).is_active)
        self.assertEqual(
            User.objects.get(username="admin").profile.role, Profile.Role.ADMIN
        )
        self.assertEqual(
            User.objects.get(username="owner").profile.role, Profile.Role.OWNER
        )
        self.assertEqual(
            User.objects.get(username="staff").profile.role, Profile.Role.STAFF
        )
