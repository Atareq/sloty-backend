# Development image: source is bind-mounted, so this layer only installs deps.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.local

WORKDIR /app

# build-essential + libpq-dev are required to build psycopg2 from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install -r requirements/dev.txt

# Source is mounted by compose; this COPY only makes the image usable standalone.
COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
