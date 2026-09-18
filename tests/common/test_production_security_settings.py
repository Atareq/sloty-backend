"""Production security settings contract.

config/settings/production.py used to contain only static-file paths, so
`manage.py check --deploy` reported HSTS, SSL-redirect and cookie warnings.
These tests pin the hardening and — more importantly — the one interaction that
would break deploys silently.
"""

import importlib

from django.test import SimpleTestCase, override_settings


def production_settings():
    module = importlib.import_module("config.settings.production")
    importlib.reload(module)
    return module


class ProductionSecuritySettingsTests(SimpleTestCase):

    def setUp(self):
        super().setUp()
        self.settings_module = production_settings()

    def test_debug_is_forced_off(self):
        """A stray DEBUG=True in .env must not publish tracebacks."""
        self.assertFalse(self.settings_module.DEBUG)

    def test_proxy_ssl_header_is_declared(self):
        # Nginx terminates TLS; without this request.is_secure() is always False.
        self.assertEqual(
            self.settings_module.SECURE_PROXY_SSL_HEADER,
            ("HTTP_X_FORWARDED_PROTO", "https"),
        )

    def test_health_probe_is_exempt_from_ssl_redirect(self):
        """The deploy gate polls http://127.0.0.1:8020/health/ over plain HTTP
        and requires HTTP 200. Without this exemption Django answers 301 and
        every production deploy fails its health check."""
        self.assertIn(r"^health/$", self.settings_module.SECURE_REDIRECT_EXEMPT)

    def test_cookies_are_secure_and_http_only(self):
        self.assertTrue(self.settings_module.SESSION_COOKIE_SECURE)
        self.assertTrue(self.settings_module.CSRF_COOKIE_SECURE)
        self.assertTrue(self.settings_module.SESSION_COOKIE_HTTPONLY)

    def test_standard_hardening_headers(self):
        self.assertTrue(self.settings_module.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(self.settings_module.X_FRAME_OPTIONS, "DENY")
        self.assertEqual(self.settings_module.SECURE_REFERRER_POLICY, "same-origin")

    def test_hsts_is_enabled_for_a_year(self):
        self.assertGreaterEqual(self.settings_module.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(self.settings_module.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_cors_defaults_to_allowing_nothing(self):
        """An unset CORS_ALLOWED_ORIGINS must not mean "allow everything"."""
        self.assertEqual(self.settings_module.CORS_ALLOWED_ORIGINS, [])
        self.assertFalse(self.settings_module.CORS_ALLOW_CREDENTIALS)


class HealthProbeRedirectTests(SimpleTestCase):
    """End-to-end proof of the exemption, not just the setting value."""

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r"^health/$"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_plain_http_health_request_returns_200_not_301(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r"^health/$"],
    )
    def test_other_plain_http_paths_still_redirect(self):
        response = self.client.get("/api/v1/egypt-locations/")
        self.assertEqual(response.status_code, 301)
