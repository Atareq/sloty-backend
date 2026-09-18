from .base import *  # noqa: F401,F403

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405

# DEBUG is never on in production, whatever .env says. A stray DEBUG=True there
# would publish tracebacks with settings and SQL to anyone who triggers a 500.
DEBUG = False

# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------
# Nginx terminates TLS and forwards over plain HTTP, so Django can only know the
# original scheme from this header. Without it, request.is_secure() is always
# False and the cookie/redirect settings below misbehave.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405

# The deploy script probes http://127.0.0.1:8020/health/ over plain HTTP with a
# Host header and REQUIRES HTTP 200. With SSL redirect on and no
# X-Forwarded-Proto on that request, Django would answer 301 and every deploy
# would fail its health gate. The probe is therefore exempt.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]

SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(  # noqa: F405
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
# Preload is opt-in: submitting to the preload list is hard to reverse.
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=False)  # noqa: F405

# ---------------------------------------------------------------------------
# Cookies and headers
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# base.py sets no origins and local.py only covers the Vite dev server, so
# production previously allowed none at all. Driven from .env here; an empty
# value keeps the safe default of allowing nothing.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")  # noqa: F405
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = env_bool("CORS_ALLOW_CREDENTIALS", default=False)  # noqa: F405
