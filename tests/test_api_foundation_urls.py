from django.urls import Resolver404, resolve, reverse
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.models import User
from apps.accounts.views import SlotyTokenObtainPairView


class ApiFoundationUrlTests(APITestCase):
    def test_anonymous_schema_url_returns_200(self):
        response = self.client.get(reverse("schema"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_anonymous_swagger_url_returns_200(self):
        response = self.client.get(reverse("swagger-ui"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_authenticated_schema_url_returns_200(self):
        user = User.objects.create_user(username="schema-user", password="pass-123")
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("schema"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_authenticated_swagger_url_returns_200(self):
        user = User.objects.create_user(username="docs-user", password="pass-123")
        self.client.force_authenticate(user=user)

        response = self.client.get(reverse("swagger-ui"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_business_endpoint_still_requires_authentication(self):
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_schema_url_resolves_to_spectacular_view(self):
        match = resolve("/api/v1/schema/")

        self.assertIs(match.func.view_class, SpectacularAPIView)

    def test_swagger_url_resolves_to_swagger_view(self):
        match = resolve("/api/v1/docs/")

        self.assertIs(match.func.view_class, SpectacularSwaggerView)

    def test_jwt_token_url_exists(self):
        match = resolve("/api/v1/auth/token/")

        self.assertIs(match.func.view_class, SlotyTokenObtainPairView)

    def test_jwt_refresh_url_exists(self):
        match = resolve("/api/v1/auth/token/refresh/")

        self.assertIs(match.func.view_class, TokenRefreshView)

    def test_old_unversioned_api_routes_are_not_kept(self):
        with self.assertRaises(Resolver404):
            resolve("/api/schema/")
