"""CAPTCHA verification, disabled by default and toggled entirely from settings.

Design constraints this module is built around:

- **Off is the cheap path.** When ``CAPTCHA_ENABLED`` is false there is no token
  read, no serializer field, and no network call. Turning the feature off must
  cost nothing, because that is the switch operations reaches for during an
  incident.
- **One entry point.** Callers only ever see ``verify_captcha``. Which provider
  is in use, how its response is shaped, and how the HTTP call is made stay
  behind this module; no domain app imports an HTTP client or names a vendor.
- **Fail closed.** If the provider cannot be reached, verification fails. The
  alternative — letting requests through whenever the provider is down — turns
  a provider outage into an open door. The escape hatch for a provider outage is
  ``CAPTCHA_ENABLED=false``, which needs no deploy.
- **No new dependency.** Verification uses ``urllib`` from the standard library.
  A single POST does not justify adding a package.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import status

from apps.common.exceptions import SlotyAPIException

logger = logging.getLogger(__name__)

CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
CAPTCHA_INVALID = "CAPTCHA_INVALID"
CAPTCHA_UNAVAILABLE = "CAPTCHA_UNAVAILABLE"


def captcha_enabled():
    """True when CAPTCHA verification should run for this deployment."""
    return bool(getattr(settings, "CAPTCHA_ENABLED", False))


def _post(url, payload, timeout):
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode())


def verify_captcha(token, *, remote_ip=None):
    """Verify a CAPTCHA token, raising SlotyAPIException on any failure.

    Returns None on success and when the feature is disabled, so callers can
    invoke it unconditionally.
    """
    if not captcha_enabled():
        return None

    if not token:
        raise SlotyAPIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CAPTCHA_REQUIRED,
            message=_("Captcha verification is required."),
        )

    payload = {"secret": settings.CAPTCHA_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        result = _post(
            settings.CAPTCHA_VERIFY_URL,
            payload,
            settings.CAPTCHA_TIMEOUT_SECONDS,
        )
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        # Deliberately fail closed. Log the class of failure only — never the
        # token, the secret, or the response body.
        logger.warning("Captcha provider unreachable: %s", type(exc).__name__)
        raise SlotyAPIException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=CAPTCHA_UNAVAILABLE,
            message=_("Captcha verification is temporarily unavailable."),
        ) from exc

    if not result.get("success"):
        raise SlotyAPIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CAPTCHA_INVALID,
            message=_("Captcha verification failed."),
        )

    # Score-based providers (reCAPTCHA v3) return a confidence score. Providers
    # that only return success/failure omit it, and the threshold does not apply.
    minimum_score = settings.CAPTCHA_MIN_SCORE
    score = result.get("score")
    if minimum_score is not None and score is not None and score < minimum_score:
        raise SlotyAPIException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=CAPTCHA_INVALID,
            message=_("Captcha verification failed."),
        )

    return None
