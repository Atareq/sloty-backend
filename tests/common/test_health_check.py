from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User


class HealthCheckTests(APITestCase):

    def test_root_health_check_is_unauthenticated(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "ok")
        self.assertEqual(response.data.get("database"), "connected")

    def test_duplicate_healthz_url_is_removed(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_health_ignores_invalid_bearer_token(self):
        response = self.client.get(
            "/health/",
            HTTP_AUTHORIZATION="Bearer not-a-valid-token",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "ok")

    def test_health_does_not_require_jwt(self):
        user = User.objects.create_user(username="health-user", password="pass-123")
        token = AccessToken.for_user(user)
        response = self.client.get(
            "/health/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_api_v1_health_check(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("status"), "ok")
