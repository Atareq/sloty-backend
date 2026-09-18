"""
Django settings for the Sloty project.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


SECRET_KEY = os.environ.get("SECRET_KEY")

DEBUG = env_bool("DEBUG", default=False)

SQL_QUERY_STATS_ENABLED = env_bool("SQL_QUERY_STATS_ENABLED", default=False)
SQL_QUERY_STATS_VERBOSE = env_bool("SQL_QUERY_STATS_VERBOSE", default=False)
SQL_QUERY_STATS_SLOW_QUERY_MS = env_float("SQL_QUERY_STATS_SLOW_QUERY_MS", 100)
SQL_QUERY_STATS_WARN_QUERY_COUNT = env_int("SQL_QUERY_STATS_WARN_QUERY_COUNT", 1)
SQL_QUERY_STATS_SLOW_REQUEST_MS = env_float("SQL_QUERY_STATS_SLOW_REQUEST_MS", 500)
SQL_QUERY_STATS_MAX_QUERY_SAMPLES = env_int("SQL_QUERY_STATS_MAX_QUERY_SAMPLES", 5)
SQL_QUERY_STATS_MAX_SQL_LENGTH = env_int("SQL_QUERY_STATS_MAX_SQL_LENGTH", 500)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django_extensions",
    "corsheaders",
    "safedelete",
    "rest_framework",
    "drf_spectacular",
    "rest_framework_simplejwt",
    "django_filters",
    "phonenumber_field",
]

APPS = [
    "apps.accounts.apps.AccountsConfig",
    "apps.profiles.apps.ProfilesConfig",
    "apps.common.apps.CommonConfig",
    "apps.clubs.apps.ClubsConfig",
    "apps.players.apps.PlayersConfig",
    "apps.courts.apps.CourtsConfig",
    "apps.bookings.apps.BookingsConfig",
    "apps.transactions.apps.TransactionsConfig",
    "apps.settlements.apps.SettlementsConfig",
    "apps.audit.apps.AuditConfig",
    "apps.dashboard.apps.DashboardConfig",
    "apps.reports.apps.ReportsConfig",
]

DEBUG_APPS = []

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + APPS + DEBUG_APPS

AUTH_USER_MODEL = "accounts.User"

# Rate limits for the unauthenticated surface only. Authenticated club traffic is
# deliberately not throttled — see apps/common/throttling.py for why.
# Every rate is env-tunable so operations can retune without a deploy. Setting a
# rate to an empty value disables that bucket.
THROTTLE_RATES = {
    "auth_login_ip": env_str("THROTTLE_AUTH_LOGIN_IP", "10/min"),
    "auth_login_username": env_str("THROTTLE_AUTH_LOGIN_USERNAME", "5/min"),
    "auth_refresh": env_str("THROTTLE_AUTH_REFRESH", "60/min"),
    "public_availability": env_str("THROTTLE_PUBLIC_AVAILABILITY", "120/min"),
    "public_static": env_str("THROTTLE_PUBLIC_STATIC", "60/min"),
}

# Number of trusted reverse proxies in front of the app. Behind Nginx this must
# be set (normally 1) or DRF reads the proxy address as the client and throttles
# every anonymous caller as if they were one user.
NUM_PROXIES = env_int("NUM_PROXIES", 0) or None

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": ("rest_framework.pagination.PageNumberPagination"),
    "EXCEPTION_HANDLER": "apps.common.exceptions.sloty_exception_handler",
    "PAGE_SIZE": 20,
    # No DEFAULT_THROTTLE_CLASSES: throttling is opt-in per view so that adding
    # a new authenticated business endpoint can never accidentally rate limit
    # staff. The rates below are consumed by the views that declare a scope.
    "DEFAULT_THROTTLE_RATES": THROTTLE_RATES,
    "NUM_PROXIES": NUM_PROXIES,
}

JWT_ACCESS_TOKEN_MINUTES = env_int("JWT_ACCESS_TOKEN_MINUTES", 60)
JWT_REFRESH_TOKEN_DAYS = env_int("JWT_REFRESH_TOKEN_DAYS", 7)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=JWT_ACCESS_TOKEN_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=JWT_REFRESH_TOKEN_DAYS),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "CHECK_REVOKE_TOKEN": True,
}

DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").strip().lower()

if DB_ENGINE in {"postgres", "postgresql"}:
    database_options = {}
    if os.environ.get("DB_SSLMODE"):
        database_options["sslmode"] = os.environ["DB_SSLMODE"]

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME"),
            "USER": os.environ.get("DB_USER"),
            "PASSWORD": os.environ.get("DB_PASSWORD"),
            "HOST": os.environ.get("DB_HOST"),
            "PORT": os.environ.get("DB_PORT"),
            "OPTIONS": database_options,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Cache backend. Redis is used when REDIS_URL is set (both Docker stacks set it);
# otherwise Django falls back to a per-process local-memory cache so plain SQLite
# development runs and the test suite keep working with no extra service.
#
# Anything that needs a *shared* counter across gunicorn workers — rate limiting
# above all — is only correct on Redis. LocMemCache is per process.
REDIS_URL = os.environ.get("REDIS_URL", "").strip()

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "sloty-locmem",
        }
    }

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.common.middleware.SQLQueryStatsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "sloty.sql": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.MinimumLengthValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.CommonPasswordValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.NumericPasswordValidator"),
    },
]

LANGUAGE_CODE = "en-us"

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = "Africa/Cairo"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

BACKEND_RELEASE_METADATA_PATH = os.environ.get(
    "BACKEND_RELEASE_METADATA_PATH",
    "/etc/sloty/backend-release.json",
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
