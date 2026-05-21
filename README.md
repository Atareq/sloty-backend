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
- `GET /api/me/` returns the authenticated user's identity, platform admin
  flag, and active club memberships for frontend club selection.
- `GET /api/users/` and `POST /api/users/` manage base user accounts for
  platform admin users.

Login is global and does not require a club slug. After login, clients call
`/api/me/`, choose one of the returned membership clubs, then call the
club-scoped endpoints with that club's slug.

## Sprint 2 Setup Endpoints

- `/api/clubs/`
- `/api/clubs/{club_slug}/memberships/`
- `/api/clubs/{club_slug}/courts/`
- `/api/clubs/{club_slug}/court-working-hours/`

## Sprint 3 Booking Endpoints

- `/api/clubs/{club_slug}/bookings/`

Useful booking list filters:

- `court`
- `status`
- `source`
- `date`
- `date_from`
- `date_to`

## Sprint 4 Transaction Endpoints

- `/api/clubs/{club_slug}/transactions/`
- `/api/clubs/{club_slug}/transactions/{id}/`

Useful transaction list filters:

- `booking`
- `court`
- `payment_method`
- `date`
- `date_from`
- `date_to`
- `created_by`

Creating the first valid transaction for a `HOLD` booking confirms it.
Transactions are immutable through the API: no edit, delete, refund, reversal,
or correction endpoint is implemented in Sprint 4. Duplicate non-blank payment
references are rejected within the same club; blank references are allowed.
Online payment gateway integration is not implemented.

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
