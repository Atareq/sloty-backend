from datetime import timedelta

import yaml
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.accounts.services import find_orphan_business_users
from apps.clubs.models import Club
from apps.courts.models import Court
from apps.players.models import PlayerProfile
from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)


class AccountAPITestCase(APITestCase):
    password = "test-pass-123"

    def create_user(self, phone_number=None, **extra_fields) -> User:
        extra_fields.setdefault("password", getattr(self, "password", "test-pass-123"))
        from tests.profile_factories import create_user as factory_create_user

        return factory_create_user(phone_number=phone_number, **extra_fields)

    def create_platform_admin(self, **extra_fields) -> User:
        user = self.create_user(**extra_fields)
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)
        return user

    def create_owner(self, clubs=None, **extra_fields) -> User:
        created_by = extra_fields.pop("created_by", None)
        user = self.create_user(**extra_fields)
        owner_profile = OwnerProfile.objects.create(
            user=user, role=Profile.Role.OWNER, created_by=created_by
        )
        if clubs:
            owner_profile.clubs.set(clubs)
        return user

    def create_staff(self, court: Court, **extra_fields) -> User:
        created_by = extra_fields.pop("created_by", None)
        user = self.create_user(**extra_fields)
        staff_profile = StaffProfile.objects.create(
            user=user,
            role=Profile.Role.STAFF,
            club=court.club,
            created_by=created_by,
        )
        StaffCourtAccess.objects.create(staff_profile=staff_profile, court=court)
        return user

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


class MeAPITests(AccountAPITestCase):
    def test_anonymous_user_gets_401(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_me_profile(self):
        creator_user = self.create_user(
            first_name="Creator",
            last_name="User",
        )
        creator = AdminProfile.objects.create(
            user=creator_user,
            role=Profile.Role.ADMIN,
        )
        staff_club = self.create_club("Champions", "champions")
        staff_court = self.create_court(staff_club, "Court 1")
        user = self.create_staff(
            staff_court,
            email="profile@example.com",
            first_name="Profile",
            last_name="User",
            phone_number="+201000000001",
            created_by=creator,
        )
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "id",
                "phone_number",
                "email",
                "first_name",
                "last_name",
                "is_active",
                "profile_role",
                "account_created_by",
            },
        )
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["phone_number"], str(user.phone_number))
        self.assertEqual(response.data["profile_role"], "STAFF")
        self.assertEqual(
            response.data["account_created_by"],
            {"id": creator.id, "name": "Creator User"},
        )
        self.assertNotIn("password", response.data)
        self.assertNotIn("hash_password", response.data)

    def test_authenticated_user_without_creator_gets_null_account_created_by(self):
        user = self.create_user()
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["account_created_by"])

    def test_me_response_prefetches_creator_and_profile(self):
        creator_user = self.create_user()
        creator = AdminProfile.objects.create(
            user=creator_user,
            role=Profile.Role.ADMIN,
        )
        staff_club = self.create_club("Query Club", "query-club")
        staff_court = self.create_court(staff_club, "Query Court")
        user = self.create_staff(
            staff_court,
            created_by=creator,
        )
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
        self.assertIn("profile_role", fields)


class JWTAPITests(AccountAPITestCase):
    def obtain_token(self, phone_number, **extra_data):
        payload = {
            "phone_number": str(phone_number),
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
        user = self.create_user()

        response = self.obtain_token(user.phone_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_token_lifetimes_follow_simple_jwt_settings(self):
        user = self.create_user()

        response = self.obtain_token(user.phone_number)

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
        user = self.create_user()

        response = self.client.post(
            reverse("token_obtain_pair"),
            {"phone_number": str(user.phone_number), "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_failures_do_not_reveal_account_state(self):
        active_user = self.create_user()
        inactive_user = self.create_user(
            is_active=False,
        )

        wrong_password_response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "phone_number": str(active_user.phone_number),
                "password": "wrong-password",
            },
            format="json",
        )
        unknown_user_response = self.client.post(
            reverse("token_obtain_pair"),
            {"phone_number": "+201099999999", "password": "wrong-password"},
            format="json",
        )
        inactive_user_response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "phone_number": str(inactive_user.phone_number),
                "password": self.password,
            },
            format="json",
        )

        self.assertEqual(
            wrong_password_response.status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(
            unknown_user_response.status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(
            inactive_user_response.status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.assertEqual(wrong_password_response.data, unknown_user_response.data)
        self.assertEqual(wrong_password_response.data, inactive_user_response.data)

    def test_malformed_login_returns_validation_error(self):
        response = self.client.post(reverse("token_obtain_pair"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("phone_number", response.data["field_errors"])
        self.assertIn("password", response.data["field_errors"])

    def test_username_is_not_accepted_as_login_identifier(self):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "anything", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone_number", response.data["field_errors"])

    def test_normalized_egyptian_phone_logs_into_the_same_account(self):
        self.create_user(phone_number="+201011122233")

        response = self.client.post(
            reverse("token_obtain_pair"),
            {"phone_number": "01011122233", "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_openapi_login_request_uses_phone_number(self):
        response = self.client.get(reverse("schema"))
        schema_doc = yaml.safe_load(response.content.decode())
        request_schema = schema_doc["paths"]["/api/v1/auth/token/"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        if "$ref" in request_schema:
            ref_name = request_schema["$ref"].rsplit("/", 1)[-1]
            request_schema = schema_doc["components"]["schemas"][ref_name]
        properties = request_schema["properties"]
        self.assertIn("phone_number", properties)
        self.assertIn("password", properties)
        self.assertNotIn("username", properties)

    def test_inactive_user_cannot_obtain_token(self):
        user = self.create_user(is_active=False)

        response = self.obtain_token(user.phone_number)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_access_token_returns_session_expired_code(self):
        user = self.create_user()
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "SESSION_EXPIRED")

    def test_stale_token_for_inactive_user_returns_user_inactive_code(self):
        user = self.create_user()
        token = AccessToken.for_user(user)
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_INACTIVE")

    def test_stale_token_for_deleted_user_returns_user_deleted_code(self):
        user = self.create_user()
        token = AccessToken.for_user(user)
        user.delete()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_DELETED")

    def test_global_normal_token_contains_base_custom_claims(self):
        user = self.create_user(
            first_name="Claims",
            last_name="User",
        )

        response = self.obtain_token(user.phone_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["user_id"], user.id)
        self.assertEqual(claims["role"], "")
        self.assertEqual(claims["name"], "Claims User")
        self.assertNotIn("club_id", claims)
        self.assertNotIn("court_id", claims)

    def test_platform_admin_token_has_admin_role(self):
        admin = self.create_platform_admin()

        response = self.obtain_token(admin.phone_number)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "ADMIN")
        self.assertEqual(claims["name"], admin.get_display_name())

    def test_platform_admin_token_with_club_slug_includes_club_context(self):
        admin = self.create_platform_admin()
        club = self.create_club("Admin Claims Club", "admin-claims-club")

        response = self.obtain_token(admin.phone_number, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], "ADMIN")
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_owner_token_with_club_slug_has_owner_claims(self):
        club = self.create_club("Owner Claims Club", "owner-claims-club")
        owner = self.create_owner(clubs=[club])

        response = self.obtain_token(owner.phone_number, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], Profile.Role.OWNER)
        self.assertEqual(claims["club_id"], club.id)
        self.assertNotIn("court_id", claims)

    def test_staff_token_with_club_slug_has_staff_and_court_claims(self):
        club = self.create_club("Staff Claims Club", "staff-claims-club")
        court = self.create_court(club, "Staff Claims Court")
        staff = self.create_staff(court)

        response = self.obtain_token(staff.phone_number, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(response.data["access"])
        self.assertEqual(claims["role"], Profile.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_invalid_club_slug_is_rejected(self):
        user = self.create_user()

        response = self.obtain_token(user.phone_number, club_slug="missing-club")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("club_slug", response.data["field_errors"])

    def test_club_slug_without_scope_is_rejected_for_non_platform_user(self):
        user = self.create_user()
        club = self.create_club("Unauthorized Claims Club", "unauthorized-claims")

        response = self.obtain_token(user.phone_number, club_slug=club.slug)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["success"], False)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")
        self.assertEqual(response.data["details"]["club_slug"], club.slug)

    def test_refresh_preserves_custom_claims(self):
        club = self.create_club("Refresh Claims Club", "refresh-claims")
        court = self.create_court(club, "Refresh Claims Court")
        staff = self.create_staff(court)
        token_response = self.obtain_token(staff.phone_number, club_slug=club.slug)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        claims = self.decode_access(refresh_response.data["access"])
        self.assertEqual(claims["role"], Profile.Role.STAFF)
        self.assertEqual(claims["club_id"], club.id)
        self.assertEqual(claims["court_id"], court.id)

    def test_invalid_refresh_token_returns_token_not_valid(self):
        response = self.client.post(
            reverse("token_refresh"),
            {"refresh": "not-a-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "TOKEN_NOT_VALID")

    def test_expired_refresh_token_returns_token_not_valid(self):
        user = self.create_user()
        refresh = RefreshToken.for_user(user)
        refresh.set_exp(lifetime=timedelta(seconds=-1))

        response = self.client.post(
            reverse("token_refresh"),
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "TOKEN_NOT_VALID")

    def test_refresh_is_repeatable_without_rotation(self):
        user = self.create_user()
        token_response = self.obtain_token(user.phone_number)

        first_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )
        second_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", first_response.data)
        self.assertIn("access", second_response.data)
        self.assertNotIn("refresh", first_response.data)
        self.assertNotIn("refresh", second_response.data)

    def test_refresh_for_inactive_user_returns_user_inactive(self):
        user = self.create_user()
        token_response = self.obtain_token(user.phone_number)
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_INACTIVE")

    def test_refresh_for_deleted_user_returns_user_deleted(self):
        user = self.create_user()
        token_response = self.obtain_token(user.phone_number)
        user.delete()

        response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "USER_DELETED")

    def test_claimless_access_and_refresh_tokens_require_reauthentication(self):
        user = self.create_user()
        refresh = RefreshToken.for_user(user)
        del refresh[api_settings.REVOKE_TOKEN_CLAIM]
        access = refresh.access_token

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        access_response = self.client.get(reverse("me"))
        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": str(refresh)},
            format="json",
        )

        self.assertEqual(access_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(access_response.data["code"], "PASSWORD_CHANGED")
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(refresh_response.data["code"], "PASSWORD_CHANGED")

    def test_stale_club_claim_does_not_bypass_revoked_access(self):
        club = self.create_club("Stale Claims Club", "stale-claims-club")
        court = self.create_court(club, "Stale Claims Court")
        staff = self.create_staff(court)
        token_response = self.obtain_token(staff.phone_number, club_slug=club.slug)

        # Revoke staff access by deleting the profile
        staff.profile.delete()

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_response.data['access']}"
        )
        response = self.client.get(
            reverse("club-court-list", kwargs={"club_slug": club.slug})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "CLUB_ACCESS_REVOKED")

    def test_user_model_does_not_store_club_scoped_role_fields(self):
        user_fields = {field.name for field in User._meta.get_fields()}

        self.assertNotIn("role", user_fields)
        self.assertNotIn("club", user_fields)
        self.assertNotIn("court", user_fields)


class PasswordChangeAPITests(AccountAPITestCase):
    new_password = "New-password-987"

    def setUp(self):
        self.user = self.create_user()

    def password_change_url(self):
        return reverse("password-change")

    def obtain_token(self, phone_number):
        return self.client.post(
            reverse("token_obtain_pair"),
            {"phone_number": str(phone_number), "password": self.password},
            format="json",
        )

    def authenticate_user(self):
        self.client.force_authenticate(user=self.user)

    def password_change_payload(self, **overrides):
        payload = {
            "current_password": self.password,
            "new_password": self.new_password,
            "new_password_confirmation": self.new_password,
        }
        payload.update(overrides)
        return payload

    def test_anonymous_user_cannot_change_password(self):
        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_can_change_own_password_without_affecting_profile(self):
        club = self.create_club("Password Change Club", "password-change-club")
        owner_profile = OwnerProfile.objects.create(
            user=self.user, role=Profile.Role.OWNER
        )
        owner_profile.clubs.add(club)
        player_user = self.create_user()
        player_profile = PlayerProfile.objects.create(
            phone_number="+201066661111",
            full_name="Password Change Player",
            user=player_user,
            role=Profile.Role.PLAYER,
        )
        original_password_hash = self.user.password
        self.authenticate_user()

        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(response.content, b"")
        self.user.refresh_from_db()
        owner_profile.refresh_from_db()
        player_profile.refresh_from_db()
        self.assertNotEqual(self.user.password, original_password_hash)
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertFalse(self.user.check_password(self.password))
        self.assertEqual(owner_profile.role, Profile.Role.OWNER)
        self.assertEqual(player_profile.user_id, player_user.id)

    def test_staff_can_change_own_password(self):
        club = self.create_club("Staff Password Club", "staff-password-club")
        court = self.create_court(club, "Staff Password Court")
        staff_user = self.create_staff(court)
        staff_profile = staff_user.profile
        self.client.force_authenticate(user=staff_user)

        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        staff_user.refresh_from_db()
        staff_profile.refresh_from_db()
        self.assertTrue(staff_user.check_password(self.new_password))
        self.assertEqual(staff_profile.role, Profile.Role.STAFF)
        self.assertEqual(staff_profile.club_id, club.id)

    def test_wrong_current_password_returns_validation_error(self):
        self.authenticate_user()

        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(current_password="wrong-password"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("current_password", response.data["field_errors"])

    def test_password_confirmation_mismatch_returns_validation_error(self):
        self.authenticate_user()

        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(
                new_password_confirmation="different-password"
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("new_password_confirmation", response.data["field_errors"])

    def test_invalid_new_password_returns_validation_error(self):
        self.authenticate_user()

        response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(
                new_password="short",
                new_password_confirmation="short",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "VALIDATION_ERROR")
        self.assertIn("new_password", response.data["field_errors"])

    def test_password_change_invalidates_existing_access_and_refresh_tokens(self):
        token_response = self.obtain_token(self.user.phone_number)
        self.authenticate_user()

        password_change_response = self.client.post(
            self.password_change_url(),
            self.password_change_payload(),
            format="json",
        )
        self.client.force_authenticate(user=None)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_response.data['access']}"
        )
        access_response = self.client.get(reverse("me"))
        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": token_response.data["refresh"]},
            format="json",
        )
        new_login_response = self.client.post(
            reverse("token_obtain_pair"),
            {
                "phone_number": str(self.user.phone_number),
                "password": self.new_password,
            },
            format="json",
        )

        self.assertEqual(
            password_change_response.status_code, status.HTTP_204_NO_CONTENT
        )
        self.assertEqual(access_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(access_response.data["code"], "PASSWORD_CHANGED")
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(refresh_response.data["code"], "PASSWORD_CHANGED")
        self.assertEqual(new_login_response.status_code, status.HTTP_200_OK)


class PlatformUserManagementAPITests(AccountAPITestCase):
    user_list_response_fields = {
        "id",
        "phone_number",
        "email",
        "first_name",
        "last_name",
        "phone_number",
        "is_active",
        "profile_role",
        "created_by",
    }

    def setUp(self):
        self.platform_admin = self.create_platform_admin()
        self.non_platform_user = self.create_user()

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
        self.assertGreaterEqual(response.data["count"], 2)
        self.assertEqual(
            set(response.data["results"][0]), self.user_list_response_fields
        )
        self.assertNotIn("password", response.data["results"][0])

        for user in (self.platform_admin, self.non_platform_user):
            filtered = self.client.get(
                reverse("user-list"),
                {"search": str(user.phone_number)},
            )
            self.assertEqual(filtered.status_code, status.HTTP_200_OK)
            listed_ids = {item["id"] for item in filtered.data["results"]}
            self.assertIn(user.id, listed_ids)

    def test_platform_admin_can_retrieve_user(self):
        self.authenticate_platform_admin()

        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.non_platform_user.id)
        self.assertEqual(
            str(response.data["phone_number"]),
            str(self.non_platform_user.phone_number),
        )
        self.assertNotIn("password", response.data)

    def test_post_users_endpoint_is_disabled_and_cannot_create_profiles(self):
        self.authenticate_platform_admin()

        before = User.objects.count()
        response = self.client.post(
            reverse("user-list"),
            {
                "phone_number": "+201099900001",
                "email": "bypassing-user@example.com",
                "password": "new-user-pass-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(User.objects.count(), before)

    def test_generic_user_endpoint_cannot_bypass_onboarding_hierarchy(self):
        self.authenticate_platform_admin()

        before = User.objects.count()
        response = self.client.post(
            reverse("user-list"),
            {
                "phone_number": "+201099900002",
                "email": "bad-onboard-user@example.com",
                "password": "new-user-pass-123",
                "role": "OWNER",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(User.objects.count(), before)

    def test_create_payload_cannot_set_staff_or_superuser_flags(self):
        self.authenticate_platform_admin()

        before = User.objects.count()
        response = self.client.post(
            reverse("user-list"),
            {
                "phone_number": "+201099900003",
                "email": "flagged-user@example.com",
                "password": "new-user-pass-123",
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(User.objects.count(), before)

    def test_platform_admin_can_patch_user_identity(self):
        self.authenticate_platform_admin()

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.non_platform_user.pk}),
            {
                "email": "updated@example.com",
                "first_name": "Updated",
                "last_name": "User",
                "phone_number": "+201000000003",
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
        self.assertEqual(set(response.data), self.user_list_response_fields)
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
        orphan = self.create_user()
        inactive_user = self.create_user()
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        platform_admin = self.create_platform_admin()
        club = self.create_club("Scoped Club", "scoped-club")
        scoped_user = self.create_owner(clubs=[club])

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
        self.owner = self.create_owner(
            clubs=[self.club_a, self.club_b],
            first_name="Tarek",
            last_name="Owner",
        )

        # Staff in Club A
        self.staff_a1 = self.create_staff(
            self.court_a,
            first_name="Ahmed",
            last_name="Mohamed",
            email="ahmed@example.com",
            phone_number="+201012345678",
        )

        self.staff_a2 = self.create_staff(
            self.court_a,
            first_name="Alo",
            last_name="Hassan",
            email="alo@example.com",
            phone_number="+201098765432",
        )

        # Staff in Club B
        self.staff_b = self.create_staff(
            self.court_b,
            first_name="Beta",
            last_name="Staff",
            email="beta@example.com",
            phone_number="+201122334455",
        )

        # Staff in Club C (unrelated club)
        self.staff_c = self.create_staff(
            self.court_c,
            first_name="Alo",
            last_name="External",
            email="gamma@example.com",
            phone_number="+201233445566",
        )

        # External Owner of Club C
        self.owner_c = self.create_owner(clubs=[self.club_c])

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

        resp_admin = self.client.get(reverse("user-list"), {"role": "ADMIN"})
        self.assertEqual(resp_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(self.list_ids(resp_admin), set())

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
        self.assertEqual(
            str(response.data["phone_number"]), str(self.staff_a1.phone_number)
        )

    def test_owner_cannot_retrieve_out_of_scope_user(self):
        self.client.force_authenticate(user=self.owner)
        resp_c = self.client.get(reverse("user-detail", kwargs={"pk": self.staff_c.pk}))
        self.assertEqual(resp_c.status_code, status.HTTP_404_NOT_FOUND)

        resp_admin = self.client.get(
            reverse("user-detail", kwargs={"pk": self.platform_admin.pk})
        )
        self.assertEqual(resp_admin.status_code, status.HTTP_404_NOT_FOUND)

        resp_owner_c = self.client.get(
            reverse("user-detail", kwargs={"pk": self.owner_c.pk})
        )
        self.assertEqual(resp_owner_c.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_cannot_create_or_patch_identity_fields(self):
        self.client.force_authenticate(user=self.owner)
        create_resp = self.client.post(
            reverse("user-list"),
            {
                "phone_number": "+201099900004",
                "email": "new-staff@example.com",
                "password": "password123",
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

        patch_resp = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.staff_a1.pk}),
            {"first_name": "Hacked"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.staff_a1.refresh_from_db()
        self.assertNotEqual(self.staff_a1.first_name, "Hacked")

    def test_owner_can_deactivate_in_scope_staff_with_is_active_only(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.staff_a1.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.staff_a1.refresh_from_db()
        self.assertFalse(self.staff_a1.is_active)
        self.assertFalse(response.data["is_active"])

    def test_owner_cannot_patch_staff_outside_owned_clubs(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": self.staff_c.pk}),
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.staff_c.refresh_from_db()
        self.assertTrue(self.staff_c.is_active)

    def test_staff_cannot_access_users_endpoint(self):
        self.client.force_authenticate(user=self.staff_a1)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unassigned_user_cannot_access_users_endpoint(self):
        user = self.create_user()
        self.client.force_authenticate(user=user)
        response = self.client.get(reverse("user-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_listing_more_staff_does_not_add_per_user_queries(self):
        # Each request starts with an uncached User.profile reverse relation.
        self.client.force_authenticate(user=User.objects.get(pk=self.owner.pk))
        with CaptureQueriesContext(connection) as small_queries:
            small = self.client.get(reverse("user-list"))
        self.assertEqual(small.status_code, status.HTTP_200_OK)
        self.assertEqual(small.data["count"], 3)

        for index in range(10):
            self.create_staff(self.court_a)

        self.client.force_authenticate(user=User.objects.get(pk=self.owner.pk))
        with CaptureQueriesContext(connection) as large_queries:
            large = self.client.get(reverse("user-list"))
        self.assertEqual(large.status_code, status.HTTP_200_OK)
        self.assertEqual(large.data["count"], 13)
        self.assertEqual(len(large.data["results"]), 13)
        self.assertEqual(len(small_queries), len(large_queries))
        self.assertLessEqual(len(large_queries), 5)
