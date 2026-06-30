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
http://127.0.0.1:8000/api/v1/docs/
```

For development convenience, `/api/v1/docs/` and `/api/v1/schema/` are public.
Business endpoints still require authentication. Use Swagger UI to inspect APIs
and test authenticated requests by providing a bearer token.

## Authentication Endpoints

- `POST /api/v1/auth/token/` obtains JWT access and refresh tokens.
- `POST /api/v1/auth/token/refresh/` refreshes an access token.
- `GET /api/v1/me/` returns the authenticated user's identity, platform admin
  flag, and active club memberships for frontend club selection.
- `GET /api/v1/users/` and `POST /api/v1/users/` manage base user accounts for
  platform admin users.

Login is global and does not require a club slug. After login, clients call
`/api/v1/me/`, choose one of the returned membership clubs, then call the
club-scoped endpoints with that club's slug.

Token obtain accepts an optional `club_slug` for frontend convenience claims.
These claims are derived at token issue time and are not stored on `User`:

```json
{
  "user_id": 1,
  "role": "STAFF",
  "name": "Demo Staff",
  "club_id": 1,
  "court_id": 1
}
```

Business APIs still verify active club access from the database through
`ClubAccessContext`; clients must not treat JWT club/court claims as authority.

## Sprint 2 Setup Endpoints

- `/api/v1/clubs/`
- `/api/v1/clubs/{club_slug}/memberships/`
- `/api/v1/clubs/{club_slug}/courts/`
- `/api/v1/clubs/{club_slug}/court-working-hours/`

## Sprint 3 Booking Endpoints

- `/api/v1/clubs/{club_slug}/bookings/`

Useful booking list filters:

- `court`
- `status`
- `source`
- `date`
- `date_from`
- `date_to`

## Sprint 4 Transaction Endpoints

- `/api/v1/clubs/{club_slug}/transactions/`
- `/api/v1/clubs/{club_slug}/transactions/{id}/`

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

## Sprint 5 Booking Lifecycle Endpoints

- `POST /api/v1/clubs/{club_slug}/bookings/{id}/cancel/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/complete/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/no-show/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/expire/`

Allowed transitions are `HOLD -> CANCELLED`, `HOLD -> EXPIRED`,
`CONFIRMED -> CANCELLED`, `CONFIRMED -> COMPLETED`, and
`CONFIRMED -> NO_SHOW`. Terminal statuses remain locked.

## Demo Seed Data

Create local/demo data for Swagger or Postman testing:

```bash
python manage.py seed_demo_data
```

The command is idempotent and local/dev only. It creates:

- `platform_admin`
- `owner_user`
- `manager_user`
- `staff_user`

All demo users use password `test-pass-123`. The demo club slug is
`demo-football-club`.

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
