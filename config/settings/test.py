from .base import *  # noqa: F401,F403

# Throttling is disabled by default under test.
#
# Business suites sign in dozens of times per module and would otherwise fail
# against a wall-clock rate window, which makes them depend on how fast the
# machine runs rather than on behaviour. Throttling itself is asserted in
# tests/common/test_throttling.py, which turns individual buckets back on with
# override_settings and clears the cache around each test.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        scope: None
        for scope in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]  # noqa: F405
    },
}
