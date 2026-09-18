"""Rate limiting contract for the unauthenticated API surface.

These tests pin three things that are easy to break silently:
  1. each open endpoint is actually throttled,
  2. the deploy health probe never is,
  3. authenticated business traffic never is.
"""

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.profiles.models import AdminProfile, Profile

LOGIN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOCATIONS_URL = "/api/v1/egypt-locations/"
HEALTH_URL = "/health/"


def rates(**overrides):
    """Build a REST_FRAMEWORK override with only the named buckets active."""
    base = {
        "auth_login_ip": None,
        "auth_login_username": None,
        "auth_refresh": None,
        "public_availability": None,
        "public_static": None,
    }
    base.update(overrides)
    return {
        "DEFAULT_AUTHENTICATION_CLASSES": (
            "rest_framework_simplejwt.authentication.JWTAuthentication",
        ),
        "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
        "EXCEPTION_HANDLER": "apps.common.exceptions.sloty_exception_handler",
        "DEFAULT_THROTTLE_RATES": base,
    }


class ThrottleTestCase(APITestCase):
    """Throttle counters live in the cache, so every test starts from empty."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()


class LoginThrottleTests(ThrottleTestCase):

    @override_settings(REST_FRAMEWORK=rates(auth_login_ip="3/min"))
    def test_login_is_throttled_per_ip(self):
        payload = {"username": "nobody", "password": "wrong-password"}

        for _ in range(3):
            response = self.client.post(LOGIN_URL, payload)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post(LOGIN_URL, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(REST_FRAMEWORK=rates(auth_login_ip="3/min"))
    def test_throttled_response_uses_the_standard_error_envelope(self):
        payload = {"username": "nobody", "password": "wrong-password"}
        for _ in range(4):
            response = self.client.post(LOGIN_URL, payload)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], "THROTTLED")
        self.assertIn("Retry-After", response.headers)

    @override_settings(REST_FRAMEWORK=rates(auth_login_username="2/min"))
    def test_login_is_throttled_per_username_across_ips(self):
        payload = {"username": "target-account", "password": "wrong-password"}

        self.client.post(LOGIN_URL, payload, REMOTE_ADDR="10.0.0.1")
        self.client.post(LOGIN_URL, payload, REMOTE_ADDR="10.0.0.2")
        # Third attempt from a third address still hits the account bucket.
        response = self.client.post(LOGIN_URL, payload, REMOTE_ADDR="10.0.0.3")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(REST_FRAMEWORK=rates(auth_login_username="2/min"))
    def test_username_throttle_does_not_leak_account_existence(self):
        """A real and a fake account must throttle identically.

        If only existing usernames were throttled, the 429 itself would be an
        account-enumeration oracle.
        """
        User.objects.create_user(username="real-account", password="irrelevant-pw")

        def attempts_until_throttled(username):
            cache.clear()
            payload = {"username": username, "password": "wrong-password"}
            count = 0
            while count < 10:
                count += 1
                response = self.client.post(LOGIN_URL, payload)
                if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    return count
            return count

        self.assertEqual(
            attempts_until_throttled("real-account"),
            attempts_until_throttled("definitely-not-a-user"),
        )

    @override_settings(REST_FRAMEWORK=rates(auth_login_ip="3/min"))
    def test_request_without_username_still_counted_by_ip(self):
        for _ in range(3):
            self.client.post(LOGIN_URL, {})
        response = self.client.post(LOGIN_URL, {})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class RefreshThrottleTests(ThrottleTestCase):

    @override_settings(REST_FRAMEWORK=rates(auth_refresh="2/min"))
    def test_refresh_is_throttled(self):
        for _ in range(2):
            self.client.post(REFRESH_URL, {"refresh": "not-a-token"})
        response = self.client.post(REFRESH_URL, {"refresh": "not-a-token"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class PublicStaticThrottleTests(ThrottleTestCase):

    @override_settings(REST_FRAMEWORK=rates(public_static="2/min"))
    def test_locations_endpoint_is_throttled_for_anonymous_callers(self):
        for _ in range(2):
            response = self.client.get(LOCATIONS_URL)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(LOCATIONS_URL)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(REST_FRAMEWORK=rates(public_static="2/min"))
    def test_authenticated_caller_is_not_throttled(self):
        user = User.objects.create_user(username="staffer", password="irrelevant-pw")
        AdminProfile.objects.create(user=user, role=Profile.Role.ADMIN)
        self.client.force_authenticate(user=user)

        for _ in range(5):
            response = self.client.get(LOCATIONS_URL)
            self.assertEqual(response.status_code, status.HTTP_200_OK)


class PublicAvailabilityThrottleTests(ThrottleTestCase):
    """The shareable player-facing link."""

    URL = "/api/v1/public/clubs/does-not-exist/courts/1/availability/"

    @override_settings(REST_FRAMEWORK=rates(public_availability="2/min"))
    def test_public_availability_is_throttled(self):
        # Throttling runs in APIView.initial(), before the view resolves the
        # club or court, so a bogus slug still exercises the limit.
        for _ in range(2):
            response = self.client.get(self.URL)
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class HealthProbeTests(ThrottleTestCase):

    @override_settings(REST_FRAMEWORK=rates(public_static="1/min"))
    def test_health_probe_is_never_throttled(self):
        """scripts/deploy-sloty-backend polls this; throttling it fails deploys."""
        for _ in range(20):
            response = self.client.get(HEALTH_URL)
            self.assertEqual(response.status_code, status.HTTP_200_OK)


class DeployCheckTests(ThrottleTestCase):
    """The system check that stops rate limiting shipping on a per-process cache."""

    LOCMEM = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "sloty-locmem",
        }
    }
    REDIS = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://redis:6379/0",
        }
    }

    def _run_check(self):
        from apps.common.checks import throttle_cache_must_be_shared

        return throttle_cache_must_be_shared(None)

    @override_settings(REST_FRAMEWORK=rates(auth_login_ip="10/min"), CACHES=LOCMEM)
    def test_error_when_throttling_on_locmem(self):
        errors = self._run_check()
        self.assertEqual([error.id for error in errors], ["common.E001"])

    @override_settings(REST_FRAMEWORK=rates(auth_login_ip="10/min"), CACHES=REDIS)
    def test_no_error_on_shared_cache(self):
        self.assertEqual(self._run_check(), [])

    @override_settings(REST_FRAMEWORK=rates(), CACHES=LOCMEM)
    def test_no_error_when_throttling_disabled(self):
        self.assertEqual(self._run_check(), [])
