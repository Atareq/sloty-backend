from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from tests.profile_factories import create_user


class HealthCheckTests(APITestCase):

    def test_root_health_check_is_unauthenticated(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "ok"})

    def test_duplicate_healthz_url_is_removed(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_health_ignores_invalid_bearer_token(self):
        response = self.client.get(
            "/health/",
            HTTP_AUTHORIZATION="Bearer not-a-valid-token",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "ok"})

    def test_health_does_not_require_jwt(self):
        user = create_user(password="pass-123")
        token = AccessToken.for_user(user)
        response = self.client.get(
            "/health/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_health_does_not_query_the_database(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(captured.captured_queries), 0)

    def test_api_v1_health_check(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "ok"})
