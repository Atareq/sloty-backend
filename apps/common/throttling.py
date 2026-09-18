"""Rate limiting for the unauthenticated API surface.

Scope of this module: the endpoints anyone on the internet can reach without a
token — login, token refresh, the public availability link, and the static
utility endpoints. Authenticated club traffic is deliberately **not** throttled.
Staff hammering the schedule on a busy evening is normal business load, not
abuse, and a limit there would break the product before it stopped an attacker.

Two important operational notes:

1. **A shared cache is mandatory.** DRF keeps throttle counters in the Django
   cache. The default LocMemCache is per process, so behind several gunicorn
   workers every worker would enforce its own separate allowance and the real
   limit becomes ``workers x rate``. Set ``REDIS_URL`` in production. The system
   check in apps/common/checks.py fails loudly when this is misconfigured.

2. **Client IP must survive the proxy.** Behind Nginx, ``REMOTE_ADDR`` is the
   proxy, so every anonymous caller would share one bucket. Set ``NUM_PROXIES``
   so DRF reads the correct entry from ``X-Forwarded-For``.
"""

import hashlib

from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class FreshRateMixin:
    """Read the configured rate from settings on every request.

    DRF binds ``SimpleRateThrottle.THROTTLE_RATES`` to the settings dict once, at
    import time. That makes the rates effectively frozen at the first import:
    ``override_settings`` in tests does not reach them, and neither would any
    runtime settings reload. Reading through ``api_settings`` each time keeps the
    configured rate and the enforced rate the same thing.
    """

    def get_rate(self):
        scope = getattr(self, "scope", None)
        if not scope:
            return None
        return api_settings.DEFAULT_THROTTLE_RATES.get(scope)


class AnonymousScopedRateThrottle(FreshRateMixin, ScopedRateThrottle):
    """Scoped throttle that only applies to anonymous callers.

    An authenticated request passes straight through. This keeps the limit
    pointed at the open surface even on endpoints (such as public availability)
    that a logged-in user may also happen to call.
    """

    def allow_request(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return True
        return super().allow_request(request, view)


class LoginIPRateThrottle(FreshRateMixin, SimpleRateThrottle):
    """Caps login attempts per source IP."""

    scope = "auth_login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LoginUsernameRateThrottle(FreshRateMixin, SimpleRateThrottle):
    """Caps login attempts per submitted username, across source IPs.

    IP throttling alone does not stop a distributed attack on one account. This
    bucket follows the account instead of the network.

    The username is hashed before it becomes part of a cache key so that the
    cache never holds a readable list of the usernames people are trying.

    This throttle applies to *every* attempt regardless of whether the account
    exists, so it cannot be used to enumerate accounts. Preserving that property
    is a hard requirement — see apps/accounts/AGENTS.md on the generic
    credential-failure response.
    """

    scope = "auth_login_username"

    def get_cache_key(self, request, view):
        username = request.data.get("username") if hasattr(request, "data") else None
        if not username:
            # Nothing to key on; the IP throttle still covers this request.
            return None
        digest = hashlib.sha256(str(username).strip().lower().encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
