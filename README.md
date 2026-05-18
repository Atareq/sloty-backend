# Sloty Backend

Sloty is a Django + Django REST Framework backend for a sports court rental
management system. The default local settings module is
`config.settings.local`.

## Local Setup

Create a local environment file from the safe example values:

```bash
cp .env.example .env
```

Update `.env` for your machine. The default example uses SQLite; set
`DB_ENGINE=postgresql` and the `DB_*` values when using PostgreSQL.

Install development dependencies:

```bash
python -m pip install -r requirements/dev.txt
```

Apply database migrations:

```bash
python manage.py migrate
```

Run the development server:

```bash
python manage.py runserver
```

Swagger UI is available at:

```text
http://127.0.0.1:8000/api/docs/
```

## Authentication Endpoints

- `POST /api/auth/token/` obtains JWT access and refresh tokens.
- `POST /api/auth/token/refresh/` refreshes an access token.
- `GET /api/me/` returns the authenticated user's profile.
- `GET /api/users/` and `POST /api/users/` manage base user accounts for
  Platform Super Admin users.

## Sprint 2 Setup Endpoints

- `/api/clubs/`
- `/api/club-memberships/`
- `/api/courts/`
- `/api/court-working-hours/`
- `/api/court-staff-assignments/`

Run tests:

```bash
pytest
```

## Development Tools

Install pre-commit hooks:

```bash
pre-commit install
```

Run all pre-commit hooks manually:

```bash
pre-commit run --all-files
```
