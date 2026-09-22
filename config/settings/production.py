from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

if DB_ENGINE not in {"postgres", "postgresql"}:
    raise ImproperlyConfigured("Production settings require DB_ENGINE=postgresql.")
