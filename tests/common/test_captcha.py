"""CAPTCHA verification contract.

Covers the disabled path (which must cost nothing), each failure mode, and the
fail-closed behaviour when the provider is unreachable. No test performs a real
network call — the HTTP POST is mocked at the module boundary.
"""

import urllib.error
from unittest import mock

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.common.captcha import captcha_enabled, verify_captcha
from apps.common.exceptions import SlotyAPIException

LOGIN_URL = "/api/v1/auth/token/"

ENABLED = {
    "CAPTCHA_ENABLED": True,
    "CAPTCHA_SECRET_KEY": "test-secret",
    "CAPTCHA_VERIFY_URL": "https://provider.test/verify",
    "CAPTCHA_TIMEOUT_SECONDS": 5.0,
    "CAPTCHA_MIN_SCORE": None,
}


class CaptchaDisabledTests(APITestCase):
    """Disabled must be the cheap path: no token required, no network call."""

    @override_settings(CAPTCHA_ENABLED=False)
    def test_captcha_enabled_reports_false(self):
        self.assertFalse(captcha_enabled())

    @override_settings(CAPTCHA_ENABLED=False)
    def test_verification_is_a_noop_without_a_token(self):
        with mock.patch("apps.common.captcha._post") as post:
            self.assertIsNone(verify_captcha(""))
        post.assert_not_called()

    @override_settings(CAPTCHA_ENABLED=False)
    def test_login_succeeds_without_a_captcha_token(self):
        User.objects.create_user(username="pilot", password="correct-horse-1")

        with mock.patch("apps.common.captcha._post") as post:
            response = self.client.post(
                LOGIN_URL, {"username": "pilot", "password": "correct-horse-1"}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        post.assert_not_called()


@override_settings(**ENABLED)
class CaptchaEnabledTests(APITestCase):

    def test_missing_token_is_rejected(self):
        with self.assertRaises(SlotyAPIException) as caught:
            verify_captcha("")
        self.assertEqual(caught.exception.api_code, "CAPTCHA_REQUIRED")

    def test_valid_token_passes(self):
        with mock.patch(
            "apps.common.captcha._post", return_value={"success": True}
        ) as post:
            self.assertIsNone(verify_captcha("a-token", remote_ip="203.0.113.7"))

        url, payload, timeout = post.call_args[0]
        self.assertEqual(url, "https://provider.test/verify")
        self.assertEqual(payload["secret"], "test-secret")
        self.assertEqual(payload["response"], "a-token")
        self.assertEqual(payload["remoteip"], "203.0.113.7")
        self.assertEqual(timeout, 5.0)

    def test_rejected_token_is_invalid(self):
        with mock.patch(
            "apps.common.captcha._post",
            return_value={"success": False, "error-codes": ["invalid-input-response"]},
        ):
            with self.assertRaises(SlotyAPIException) as caught:
                verify_captcha("a-token")

        self.assertEqual(caught.exception.api_code, "CAPTCHA_INVALID")
        self.assertEqual(caught.exception.status_code, status.HTTP_400_BAD_REQUEST)

    def test_provider_timeout_fails_closed(self):
        with mock.patch(
            "apps.common.captcha._post", side_effect=urllib.error.URLError("timed out")
        ):
            with self.assertRaises(SlotyAPIException) as caught:
                verify_captcha("a-token")

        self.assertEqual(caught.exception.api_code, "CAPTCHA_UNAVAILABLE")
        self.assertEqual(
            caught.exception.status_code, status.HTTP_503_SERVICE_UNAVAILABLE
        )

    def test_malformed_provider_response_fails_closed(self):
        with mock.patch(
            "apps.common.captcha._post", side_effect=ValueError("not json")
        ):
            with self.assertRaises(SlotyAPIException) as caught:
                verify_captcha("a-token")
        self.assertEqual(caught.exception.api_code, "CAPTCHA_UNAVAILABLE")

    def test_remote_ip_is_omitted_when_unknown(self):
        with mock.patch(
            "apps.common.captcha._post", return_value={"success": True}
        ) as post:
            verify_captcha("a-token")
        self.assertNotIn("remoteip", post.call_args[0][1])


@override_settings(**{**ENABLED, "CAPTCHA_MIN_SCORE": 0.5})
class CaptchaScoreTests(APITestCase):
    """Score thresholds apply only to providers that return a score."""

    def test_low_score_is_rejected(self):
        with mock.patch(
            "apps.common.captcha._post", return_value={"success": True, "score": 0.1}
        ):
            with self.assertRaises(SlotyAPIException) as caught:
                verify_captcha("a-token")
        self.assertEqual(caught.exception.api_code, "CAPTCHA_INVALID")

    def test_sufficient_score_passes(self):
        with mock.patch(
            "apps.common.captcha._post", return_value={"success": True, "score": 0.9}
        ):
            self.assertIsNone(verify_captcha("a-token"))

    def test_provider_without_a_score_is_unaffected(self):
        with mock.patch(
            "apps.common.captcha._post", return_value={"success": True}
        ):
            self.assertIsNone(verify_captcha("a-token"))


@override_settings(**ENABLED)
class LoginCaptchaIntegrationTests(APITestCase):
    """The login endpoint honours the toggle end to end."""

    def setUp(self):
        super().setUp()
        User.objects.create_user(username="pilot", password="correct-horse-1")

    def test_login_without_token_returns_captcha_required(self):
        response = self.client.post(
            LOGIN_URL, {"username": "pilot", "password": "correct-horse-1"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "CAPTCHA_REQUIRED")
        self.assertFalse(response.data["success"])

    def test_login_with_valid_token_succeeds(self):
        with mock.patch("apps.common.captcha._post", return_value={"success": True}):
            response = self.client.post(
                LOGIN_URL,
                {
                    "username": "pilot",
                    "password": "correct-horse-1",
                    "captcha_token": "a-token",
                },
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        # The token is write-only and must never be echoed back.
        self.assertNotIn("captcha_token", response.data)

    def test_captcha_is_checked_before_credentials(self):
        """A bad CAPTCHA must not also reveal whether the password was right."""
        with mock.patch("apps.common.captcha._post", return_value={"success": False}):
            response = self.client.post(
                LOGIN_URL,
                {
                    "username": "pilot",
                    "password": "definitely-wrong",
                    "captcha_token": "a-token",
                },
            )

        self.assertEqual(response.data["code"], "CAPTCHA_INVALID")
