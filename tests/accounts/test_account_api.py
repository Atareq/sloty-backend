from datetime import timedelta

import yaml
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.clubs.models import Club, ClubMembership
from apps.courts.models import Court


class AccountAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, username: str, **extra_fields) -> User:
        return User.objects.create_user(
            username=username,
            password=self.password,
            **extra_fields,
        )

    def create_platform_admin(self, username="platform-admin") -> User:
        return self.create_user(username=username, is_platform_admin=True)

    def create_club(self, name: str, slug: str, **extra_fields) -> Club:
        data = {
            "name": name,
            "slug": slug,
            "governorate": "ASSIUT",
            "city": "ASSIUT_MARKAZ",
        }
        data.update(extra_fields)
        return Club.objects.create(**data)

    def create_court(self, club: Club, name: str, **extra_fields) -> Court:
        data = {
            "club": club,
            "name": name,
            "default_price": "250.00",
        }
        data.update(extra_fields)
        return Court.objects.create(**data)

    def create_membership(self, user, club, role, court=None):
        return ClubMembership.objects.create(
            club=club,
            user=user,
            role=role,
            court=court,
        )


class MeAPITests(AccountAPITestCase):
    def test_anonymous_user_gets_401(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_profile_with_active_memberships(self):
        creator = self.create_user(
            username="profile-creator",
            first_name="Creator",
            last_name="User",
        )
        user = self.create_user(
            username="profile-user",
            email="profile@example.com",
            first_name="Profile",
            last_name="User",
            phone_number="+201000000001",
            created_by=creator,
        )
        owner_club = self.create_club("El Nasr", "el-nasr")
        staff_club = self.create_club("Champions", "champions")
        staff_court = self.create_court(staff_club, "Court 1")
        owner_membership = self.create_membership(
            user,
            owner_club,
            ClubMembership.Role.OWNER,
        )
        staff_membership = self.create_membership(
            user,
            staff_club,
            ClubMembership.Role.STAFF,
            court=staff_court,
        )
        inactive_club = self.create_club("Inactive Scope", "inactive-scope")
        ClubMembership.objects.create(
            club=inactive_club,
            user=user,
            role=ClubMembership.Role.OWNER,
            is_active=False,
        )
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "id",
                "username",
                "email",
                "first_name",
                "last_name",
                "phone_number",
                "is_active",
                "is_platform_admin",
                "account_created_by",
                "memberships",
            },
        )
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["username"], user.username)
        self.assertFalse(response.data["is_platform_admin"])
        self.assertEqual(
            response.data["account_created_by"],
            {"id": creator.id, "name": "Creator User"},
        )
        self.assertNotIn("password", response.data)

        memberships = {item["id"]: item for item in response.data["memberships"]}
        self.assertEqual(set(memberships), {owner_membership.id, staff_membership.id})
        self.assertEqual(memberships[owner_membership.id]["role"], "OWNER")
        self.assertEqual(memberships[owner_membership.id]["club"]["slug"], "el-nasr")
        self.assertIsNone(memberships[owner_membership.id]["court"])
        self.assertEqual(
            memberships[owner_membership.id]["permissions"],
            {
                "can_change_pricing": True,
                "can_manage_working_hours": True,
                "can_manage_settlements": True,
            },
        )
        self.assertEqual(memberships[staff_membership.id]["role"], "STAFF")
        self.assertEqual(
            memberships[staff_membership.id]["club"]["slug"],
            "champions",
        )
        self.assertEqual(
            memberships[staff_membership.id]["court"],
            {"id": staff_court.id, "name": staff_court.name},
        )
        self.assertEqual(
            memberships[staff_membership.id]["permissions"],
            {
                "can_change_pricing": False,
                "can_manage_working_hours": False,
                "can_manage_settlements": False,
            },
        )

    def test_authenticated_user_without_creator_gets_null_account_created_by(self):
        user = self.create_user(username="profile-user-without-creator")
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["account_created_by"])

    def test_me_response_prefetches_creator_and_active_memberships(self):
        creator = self.create_user(username="query-creator")
        user = self.create_user(username="query-profile-user", created_by=creator)
        club = self.create_club("Query Club", "query-club")
        court = self.create_court(club, "Query Court")
        self.create_membership(user, club, ClubMembership.Role.STAFF, court=court)
        self.client.force_authenticate(user=user)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 2)

    def test_openapi_documents_me_nested_response_fields(self):
        response = self.client.get(reverse("schema"))
        schema_doc = yaml.safe_load(response.content.decode())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fields = schema_doc["components"]["schemas"]["UserMe"]["properties"]
        self.assertTrue(fields["account_created_by"]["nullable"])
        self.assertEqual(
            fields["account_created_by"]["allOf"][0]["$ref"],
            "#/components/schemas/AccountCreator",
        )
        self.assertEqual(fields["memberships"]["type"], "array")
        self.assertEqual(
            fields["memberships"]["items"]["$ref"],
            "#/components/schemas/UserMembership",
        )


class JWTAPITests(AccountAPITestCase):
    def obtain_token(self, username, **extra_data):
        payload = {
            "username": username,
            "password": self.password,
        }
        payload.update(extra_data)
        return self.client.post(
            reverse("token_obtain_pair"),
            payload,
            format="json",
        )

    def decode_access(self, token):
        return AccessToken(token)

    def test_active_user_can_obtain_token_without_club_slug(self):
        user = self.create_user(username="active-token-user")

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_token_lifetimes_follow_simple_jwt_settings(self):
        user = self.create_user(username="lifetime-token-user")

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        access = AccessToken(response.data["access"])
        refresh = RefreshToken(response.data["refresh"])
        self.assertAlmostEqual(
            access["exp"] - access["iat"],
            settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds(),
            delta=2,
        )
        self.assertAlmostEqual(
            refresh["exp"] - refresh["iat"],
            settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds(),
            delta=2,
        )
        self.assertEqual(
            settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"],
            timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES),
        )
        self.assertEqual(
            settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"],
            timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS),
        )
        self.assertLess(
            settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"],
            settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"],
        )

    def test_invalid_credentials_fail(self):
        user = self.create_user(username="invalid-token-user")

        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": user.username, "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_cannot_obtain_token(self):
        user = self.create_user(username="inactive-token-user", is_active=False)

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_access_token_returns_session_expired_code(self):
        user = self.create_user(username="expired-access-user")
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "SESSION_EXPIRED")

    def test_stale_token_for_inactive_user_returns_user_inactive_code(self):
        user = self.create_user(username="stale-inactive-user")
        token = AccessToken.for_user(user)
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_INACTIVE")

    def test_stale_token_for_deleted_user_returns_user_deleted_code(self):
        user = self.create_user(username="stale-deleted-user")
        token = AccessToken.for_user(user)
        user.delete()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_DELETED")

    def test_global_normal_token_contains_base_custom_claims(self):
        user = self.create_user(
            username="claims-user",
            first_name="Claims",
            last_name="User",
        )

        response = self.obtain_token(user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["user_id"], user.id)
        self.assertEqual(claims["role"], "")
        self.assertEqual(claims["name"], "Claims User")
        self.assertNotIn("club_id", claims)
        self.assertNotIn("court_id", claims)

    def test_platform_admin_token_has_platform_role(self):
        admin = self.create_platform_admin(username="claims-admin")

        response = self.obtain_token(admin.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "PLATFORM_ADMIN")
        self.assertEqual(claims["name"], admin.username)

    def test_platform_admin_token_with_club_slug_includes_club_context(self):
        admin = self.create_platform_admin(username="claims-admin-club")
        club = self.create_club("Admin Claims Club", "admin-claims-club")

        response = self.obtain_token(admin.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "PLATFORM_ADMIN")
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_owner_token_with_club_slug_has_owner_claims(self):
        owner = self.create_user(username="claims-owner")
        club = self.create_club("Owner Claims Club", "owner-claims-club")
        self.create_membership(owner, club, ClubMembership.Role.OWNER)

        response = self.obtain_token(owner.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.OWNER)
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_manager_token_with_club_slug_has_manager_claims(self):
        manager = self.create_user(username="claims-manager")
        club = self.create_club("Manager Claims Club", "manager-claims-club")
        self.create_membership(manager, club, ClubMembership.Role.MANAGER)

        response = self.obtain_token(manager.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.MANAGER)
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_staff_token_with_club_slug_has_staff_and_court_claims(self):
        staff = self.create_user(username="claims-staff")
        club = self.create_club("Staff Claims Club", "staff-claims-club")
        court = self.create_court(club, "Staff Claims Court")
        self.create_membership(staff, club, ClubMembership.Role.STAFF, court=court)

        response = self.obtain_token(staff.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_invalid_club_slug_is_rejected(self):
        user = self.create_user(username="invalid-club-token-user")

        response = self.obtain_token(user.username, club_slug="missing-club")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("club_slug", response.data["field_errors"])

    def test_club_slug_without_membership_is_rejected_for_non_platform_user(self):
        user = self.create_user(username="unauthorized-club-token-user")
        club = self.create_club("Unauthorized Claims Club", "unauthorized-claims")

        response = self.obtain_token(user.username, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")
        self.assertEqual(response.data["details"]["club_slug"], club.slug)

    def test_refresh_preserves_custom_claims(self):
        staff = self.create_user(username="refresh-claims-staff")
        club = self.create_club("Refresh Claims Club", "refresh-claims")
        court = self.create_court(club, "Refresh Claims Court")
        self.create_membership(staff, club, ClubMembership.Role.STAFF, court=court)
        token_response = self.obtain_token(staff.username, club_slug=club.slug)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(refresh_response.data["access"])
        self.assertEqual(claims["role"], ClubMembership.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_user_model_does_not_store_club_scoped_role_fields(self):
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)


class PlatformUserManagementAPITests(AccountAPITestCase):
    user_list_response_fields = {
        "id",
        "username",
        "email",
        "first_name",
        "last_name",
        "phone_number",
        "is_active",
        "is_platform_admin",
        "created_by",
    }

    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.non_platform_user = self.create_user(username="normal-user")

    def authenticate_platform_admin(self):
        self.client.force_authenticate(user=self.platform_admin)

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_platform_user_cannot_list_users(self):
        self.client.force_authenticate(user=self.non_platform_user)

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_platform_admin_can_list_users(self):
        self.authenticate_platform_admin()

        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            set(response.data["results"][0]), self.user_list_response_fields
        )
        self.assertNotIn("password", response.data["results"][0])

    def test_platform_admin_can_retrieve_user(self):
        self.authenticate_platform_admin()

        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.non_platform_user.id)
        self.assertEqual(response.data["username"], self.non_platform_user.username)
        self.assertNotIn("password", response.data)

    def test_platform_admin_cannot_create_non_platform_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "created-user",
                "password": "new-user-pass-123",
                "email": "created@example.com",
                "first_name": "Created",
                "last_name": "User",
                "phone_number": "+201000000002",
                "is_active": True,
                "is_platform_admin": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["field_errors"]["non_field_errors"][0]["message"],
            "Club users must be created through a club-scoped membership endpoint.",
        )
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertFalse(User.objects.filter(username="created-user").exists())

    def test_platform_admin_can_create_platform_admin_user(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "created-admin",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="created-admin")
        self.assertTrue(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.created_by, self.platform_admin)
        self.assertTrue(response.data["is_platform_admin"])
        self.assertNotIn("password", response.data)

    def test_user_create_rejects_role_club_and_court_fields(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "bad-scope-user",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
                "role": ClubMembership.Role.STAFF,
                "club": 1,
                "court": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("role", response.data["field_errors"])
        self.assertIn("club", response.data["field_errors"])
        self.assertIn("court", response.data["field_errors"])
        self.assertFalse(User.objects.filter(username="bad-scope-user").exists())

    def test_create_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        response = self.client.post(
            reverse("user-list"),
            {
                "username": "flagged-user",
                "password": "new-user-pass-123",
                "is_platform_admin": True,
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username="flagged-user")
        self.assertTrue(user.is_platform_admin)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotIn("is_staff", response.data)
        self.assertNotIn("is_superuser", response.data)
        self.assertNotIn("password", response.data)

    def test_platform_admin_can_patch_user_identity_and_platform_flag(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {
                "email": "updated@example.com",
                "first_name": "Updated",
                "last_name": "User",
                "phone_number": "+201000000003",
                "is_platform_admin": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertEqual(self.non_platform_user.email, "updated@example.com")
        self.assertEqual(self.non_platform_user.first_name, "Updated")
        self.assertEqual(self.non_platform_user.last_name, "User")
        self.assertEqual(
            str(self.non_platform_user.phone_number),
            "+201000000003",
        )
        self.assertTrue(self.non_platform_user.is_platform_admin)
        self.assertEqual(set(response.data), self.user_list_response_fields)
        self.assertTrue(response.data["is_platform_admin"])
        self.assertNotIn("password", response.data)

    def test_platform_admin_can_deactivate_user(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.non_platform_user.refresh_from_db()
        self.assertFalse(self.non_platform_user.is_active)
        self.assertFalse(response.data["is_active"])

    def test_delete_user_is_not_allowed(self):
        self.authenticate_platform_admin()

        response = self.client.delete(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class OrphanBusinessUserIntegrityTests(AccountAPITestCase):
    def test_find_orphan_business_users_identifies_active_non_platform_orphans(self):
        orphan = self.create_user(username="orphan-business-user")
        inactive_user = self.create_user(username="inactive-business-user")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        platform_admin = self.create_platform_admin("orphan-platform-admin")
        club = self.create_club("Scoped Club", "scoped-club")
        scoped_user = self.create_user(username="scoped-business-user")
        self.create_membership(scoped_user, club, ClubMembership.Role.OWNER)

        orphan_ids = set(find_orphan_business_users().values_list("id", flat=True))

        self.assertIn(orphan.id, orphan_ids)
        self.assertNotIn(inactive_user.id, orphan_ids)
        self.assertNotIn(platform_admin.id, orphan_ids)
        self.assertNotIn(scoped_user.id, orphan_ids)


class OwnerUserScopingAPITests(AccountAPITestCase):
    def setUp(self):
        super().setUp()
        self.club_a = self.create_club("Club Alpha", "club-alpha")
        self.club_b = self.create_club("Club Beta", "club-beta")
        self.club_c = self.create_club("Club Gamma", "club-gamma")

        self.court_a = self.create_court(self.club_a, "Court Alpha")
        self.court_b = self.create_court(self.club_b, "Court Beta")
        self.court_c = self.create_court(self.club_c, "Court Gamma")

        # Owner of Club A and Club B
        self.owner = self.create_user(
            "owner_user",
            first_name="Tarek",
            last_name="Owner",
        )
        self.create_membership(self.owner, self.club_a, ClubMembership.Role.OWNER)
        self.create_membership(self.owner, self.club_b, ClubMembership.Role.OWNER)

        # Manager of Club A
        self.manager_a = self.create_user(
            "manager_a",
            first_name="Mona",
            last_name="Manager",
        )
        self.create_membership(self.manager_a, self.club_a, ClubMembership.Role.MANAGER)

        # Staff in Club A
        self.staff_a1 = self.create_user(
            "staff_ahmed",
            first_name="Ahmed",
            last_name="Mohamed",
            email="ahmed@example.com",
            phone_number="+201012345678",
        )
        self.create_membership(
            self.staff_a1,
            self.club_a,
            ClubMembership.Role.STAFF,
            court=self.court_a,
        )

        self.staff_a2 = self.create_user(
            "staff_alo",
            first_name="Alo",
            last_name="Hassan",
            email="alo@example.com",
            phone_number="+201098765432",
        )
        self.create_membership(
            self.staff_a2,
            self.club_a,
            ClubMembership.Role.STAFF,
            court=self.court_a,
        )

        # Staff in Club B
        self.staff_b = self.create_user(
            "staff_beta",
            first_name="Beta",
            last_name="Staff",
            email="beta@example.com",
            phone_number="+201122334455",
        )
        self.create_membership(
            self.staff_b,
            self.club_b,
            ClubMembership.Role.STAFF,
            court=self.court_b,
        )

        # Staff in Club C (unrelated club)
        self.staff_c = self.create_user(
            "staff_gamma",
            first_name="Alo",
            last_name="External",
            email="gamma@example.com",
            phone_number="+201233445566",
        )
        self.create_membership(
            self.staff_c,
            self.club_c,
            ClubMembership.Role.STAFF,
            court=self.court_c,
        )

        # External Owner of Club C
        self.owner_c = self.create_user("owner_c")
        self.create_membership(self.owner_c, self.club_c, ClubMembership.Role.OWNER)

        # Platform Admin
        self.platform_admin = self.create_platform_admin()

    def list_ids(self, response):
        return {item["id"] for item in response.data["results"]}

    def test_owner_can_list_staff_across_owned_clubs(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self.list_ids(response)
        self.assertIn(self.staff_a1.id, ids)
        self.assertIn(self.staff_a2.id, ids)
        self.assertIn(self.staff_b.id, ids)
        self.assertNotIn(self.staff_c.id, ids)
        self.assertNotIn(self.manager_a.id, ids)
        self.assertNotIn(self.owner.id, ids)
        self.assertNotIn(self.owner_c.id, ids)
        self.assertNotIn(self.platform_admin.id, ids)

    def test_owner_can_search_staff_by_name(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("user-list"), {"search": "alo"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self.list_ids(response)
        self.assertEqual(ids, {self.staff_a2.id})
        self.assertNotIn(self.staff_c.id, ids)

    def test_owner_can_search_staff_by_phone_variants(self):
        self.client.force_authenticate(user=self.owner)
        resp_local = self.client.get(reverse("user-list"), {"search": "01012345678"})
        self.assertEqual(resp_local.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_local), {self.staff_a1.id})

        resp_intl = self.client.get(reverse("user-list"), {"search": "+201012345678"})
        self.assertEqual(self.list_ids(resp_intl), {self.staff_a1.id})

        resp_spaced = self.client.get(reverse("user-list"), {"search": "010 1234 5678"})
        self.assertEqual(self.list_ids(resp_spaced), {self.staff_a1.id})

    def test_owner_cannot_escape_scope_with_role_filter(self):
        self.client.force_authenticate(user=self.owner)
        resp_owner = self.client.get(reverse("user-list"), {"role": "OWNER"})
        self.assertEqual(resp_owner.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_owner), set())

        resp_mgr = self.client.get(reverse("user-list"), {"role": "MANAGER"})
        self.assertEqual(resp_mgr.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_mgr), set())

        resp_staff = self.client.get(reverse("user-list"), {"role": "STAFF"})
        self.assertEqual(resp_staff.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.list_ids(resp_staff),
            {self.staff_a1.id, self.staff_a2.id, self.staff_b.id},
        )

    def test_owner_cannot_escape_scope_with_club_filter(self):
        self.client.force_authenticate(user=self.owner)
        resp_c = self.client.get(reverse("user-list"), {"club": "club-gamma"})
        self.assertEqual(resp_c.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_c), set())

        resp_a = self.client.get(reverse("user-list"), {"club": "club-alpha"})
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_a), {self.staff_a1.id, self.staff_a2.id})

        resp_b = self.client.get(reverse("user-list"), {"club": self.club_b.id})
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_b), {self.staff_b.id})

    def test_owner_can_filter_by_is_active(self):
        self.staff_a2.is_active = False
        self.staff_a2.save(update_fields=["is_active"])

        self.client.force_authenticate(user=self.owner)
        resp_active = self.client.get(reverse("user-list"), {"is_active": "true"})
        self.assertEqual(resp_active.status_code, status.HTTP_200_OK)
        self.assertIn(self.staff_a1.id, self.list_ids(resp_active))
        self.assertNotIn(self.staff_a2.id, self.list_ids(resp_active))

        resp_inactive = self.client.get(reverse("user-list"), {"is_active": "false"})
        self.assertEqual(resp_inactive.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_inactive), {self.staff_a2.id})

    def test_owner_can_retrieve_own_staff_detail(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.staff_a1.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.staff_a1.id)
        self.assertEqual(response.data["username"], self.staff_a1.username)

    def test_owner_cannot_retrieve_out_of_scope_user(self):
        self.client.force_authenticate(user=self.owner)
        resp_c = self.client.get(reverse("user-detail", kwargs={"pk": self.staff_c.pk}))
        self.assertEqual(resp_c.status_code, status.HTTP_404_NOT_FOUND)

        resp_mgr = self.client.get(
            reverse("user-detail", kwargs={"pk": self.manager_a.pk})
        )
        self.assertEqual(resp_mgr.status_code, status.HTTP_404_NOT_FOUND)

        resp_admin = self.client.get(
            reverse("user-detail", kwargs={"pk": self.platform_admin.pk})
        )
        self.assertEqual(resp_admin.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_cannot_create_or_patch_users(self):
        self.client.force_authenticate(user=self.owner)
        create_resp = self.client.post(
            reverse("user-list"),
            {
                "username": "new_staff",
                "password": "password123",
                "is_platform_admin": False,
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

        patch_resp = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.staff_a1.pk}),
            {"first_name": "Hacked"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_access_users_endpoint(self):
        self.client.force_authenticate(user=self.manager_a)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        search_response = self.client.get(reverse("user-list"), {"search": "alo"})
        self.assertEqual(search_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_cannot_access_users_endpoint(self):
        self.client.force_authenticate(user=self.staff_a1)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_soft_deleted_staff_membership_is_excluded(self):
        from django.utils import timezone

        ClubMembership.objects.filter(user=self.staff_a2, club=self.club_a).update(
            deleted_at=timezone.now()
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self.list_ids(response)
        self.assertNotIn(self.staff_a2.id, ids)

    def test_deactivated_owner_cannot_access_users_endpoint(self):
        ClubMembership.objects.filter(user=self.owner).update(is_active=False)
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_query_count_is_bounded_for_list(self):
        self.client.force_authenticate(user=self.owner)
        with CaptureQueriesContext(connection) as capture_three:
            resp_three = self.client.get(reverse("user-list"))
        self.assertEqual(resp_three.status_code, status.HTTP_200_OK)
        queries_three = len(capture_three)

        for i in range(10):
            extra_staff = self.create_user(f"extra_staff_{i}")
            self.create_membership(
                extra_staff,
                self.club_a,
                ClubMembership.Role.STAFF,
                court=self.court_a,
            )

        with CaptureQueriesContext(connection) as capture_thirteen:
            resp_thirteen = self.client.get(reverse("user-list"))
        self.assertEqual(resp_thirteen.status_code, status.HTTP_200_OK)
        queries_thirteen = len(capture_thirteen)

        self.assertEqual(queries_three, queries_thirteen)
