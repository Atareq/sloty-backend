import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User

VALID_COMMIT = "91bd3aa28e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b"
VALID_RELEASE = "v0.7.0"
VERSION_URL = "/api/system/version/"


class BackendVersionAPITests(APITestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.metadata_path = str(Path(tmp.name) / "backend-release.json")
        settings_override = override_settings(
            BACKEND_RELEASE_METADATA_PATH=self.metadata_path
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.user = User.objects.create_user(
            username="version-user",
            password="test-pass-123",
        )
        self.client.force_authenticate(user=self.user)

    def _write_metadata(self, payload, *, raw=None):
        path = Path(self.metadata_path)
        if raw is None:
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            path.write_text(raw, encoding="utf-8")

    def _get(self):
        return self.client.get(VERSION_URL)

    def test_valid_metadata_returns_release_identity(self):
        self._write_metadata(
            {
                "service": "sloty-backend",
                "release": VALID_RELEASE,
                "commit": VALID_COMMIT,
            }
        )

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["service"], "sloty-backend")
        self.assertEqual(response.data["release"], VALID_RELEASE)
        self.assertEqual(response.data["commit"], VALID_COMMIT)

    def test_missing_metadata_returns_null_release_fields(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "service": "sloty-backend",
                "release": None,
                "commit": None,
            },
        )

    def test_malformed_metadata_is_safe(self):
        self._write_metadata(None, raw="{not-json")

        response = self._get()
        body = response.content.decode()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["service"], "sloty-backend")
        self.assertIsNone(response.data["release"])
        self.assertIsNone(response.data["commit"])
        self.assertNotIn("Traceback", body)
        self.assertNotIn("JSONDecodeError", body)
        self.assertNotIn("/etc/sloty", body)
        self.assertNotIn(str(self.metadata_path), body)

    def test_invalid_release_is_not_returned(self):
        self._write_metadata(
            {
                "service": "sloty-backend",
                "release": "production",
                "commit": VALID_COMMIT,
            }
        )

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["release"])
        self.assertIsNone(response.data["commit"])
        self.assertNotEqual(response.data.get("release"), "production")

    def test_invalid_commit_is_not_returned(self):
        self._write_metadata(
            {
                "service": "sloty-backend",
                "release": VALID_RELEASE,
                "commit": "abc",
            }
        )

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["release"])
        self.assertIsNone(response.data["commit"])
        self.assertNotEqual(response.data.get("commit"), "abc")

    def test_anonymous_access_is_denied(self):
        self.client.force_authenticate(user=None)

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("release", response.data)

    def test_authenticated_access_is_allowed(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["service"], "sloty-backend")

    def test_endpoint_does_not_query_application_models(self):
        self._write_metadata(
            {
                "service": "sloty-backend",
                "release": VALID_RELEASE,
                "commit": VALID_COMMIT,
            }
        )

        with CaptureQueriesContext(connection) as captured:
            response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(captured.captured_queries), 0)

    def test_response_is_not_stored_in_caches(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_named_url_matches_system_version_path(self):
        self.assertEqual(reverse("system_version"), VERSION_URL)

    def test_optional_deployed_at_is_returned_when_valid(self):
        self._write_metadata(
            {
                "service": "sloty-backend",
                "release": VALID_RELEASE,
                "commit": VALID_COMMIT,
                "deployed_at": "2026-09-18T00:15:32Z",
            }
        )

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deployed_at"], "2026-09-18T00:15:32Z")
