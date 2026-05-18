from django.test import SimpleTestCase
from django.urls import resolve, reverse
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import status
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


class ApiFoundationUrlTests(SimpleTestCase):
    def test_schema_url_returns_200(self):
        response = self.client.get(reverse("schema"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_swagger_url_returns_200(self):
        response = self.client.get(reverse("swagger-ui"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_schema_url_resolves_to_spectacular_view(self):
        match = resolve("/api/schema/")

        self.assertIs(match.func.view_class, SpectacularAPIView)

    def test_swagger_url_resolves_to_swagger_view(self):
        match = resolve("/api/docs/")

        self.assertIs(match.func.view_class, SpectacularSwaggerView)

    def test_jwt_token_url_exists(self):
        match = resolve("/api/auth/token/")

        self.assertIs(match.func.view_class, TokenObtainPairView)

    def test_jwt_refresh_url_exists(self):
        match = resolve("/api/auth/token/refresh/")

        self.assertIs(match.func.view_class, TokenRefreshView)
