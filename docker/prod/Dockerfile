# Production image: multi-stage so compilers never reach the runtime layer.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install --prefix=/install -r requirements/base.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

# libpq5 is the psycopg2 runtime library; the -dev headers stay in the builder.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 1000 sloty

COPY --from=builder /install /usr/local
COPY . .

# Static files are baked into the image. SECRET_KEY is required for Django to
# boot but is never used to sign anything during collectstatic, so a throwaway
# build-time value is safe and keeps real secrets out of image layers.
RUN SECRET_KEY=build-time-only-not-a-secret \
    python manage.py collectstatic --noinput \
    && chown -R sloty:sloty /app

USER sloty

EXPOSE 8000

# Migrations are deliberately NOT run here. Schema changes stay an explicit,
# operator-driven step (see docker/README.md).
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-60} --access-logfile - --error-logfile -"]
