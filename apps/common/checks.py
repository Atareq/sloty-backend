"""Deployment-time system checks for cross-cutting infrastructure."""

from django.conf import settings
from django.core.checks import Error, Tags, register

LOCMEM_BACKEND = "django.core.cache.backends.locmem.LocMemCache"


@register(Tags.caches, deploy=True)
def throttle_cache_must_be_shared(app_configs, **kwargs):
    """Refuse to deploy rate limiting on a per-process cache.

    DRF stores throttle counters in the Django cache. On LocMemCache each
    gunicorn worker keeps its own counters, so the effective limit silently
    becomes ``workers x configured rate`` — a rate limit that does not limit.
    This check turns that silent misconfiguration into a loud one.

    It only runs under ``manage.py check --deploy``, so local SQLite work and
    the test suite are unaffected.
    """
    rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
    if not any(rates.values()):
        return []

    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if backend != LOCMEM_BACKEND:
        return []

    return [
        Error(
            "Rate limiting is configured but the default cache is LocMemCache.",
            hint=(
                "Throttle counters would be per gunicorn worker, making the "
                "real limit workers x rate. Set REDIS_URL so CACHES uses Redis."
            ),
            id="common.E001",
        )
    ]
