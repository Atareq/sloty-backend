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
- `GET /api/v1/users/` manages base user accounts for platform admin users.
- `POST /api/v1/users/` creates platform admin users only.

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

## Club User Onboarding

Platform admins can be created through `/api/v1/users/`. Club users must be
created through `/api/v1/clubs/{club_slug}/memberships/`, which creates the
`User` and active `ClubMembership` together in one transaction.

OWNER, MANAGER, and STAFF roles live on `ClubMembership`, not `User`. STAFF
memberships require a court. OWNER and MANAGER memberships are club-level and
must not include a court.

## Egypt Locations and Club Address Fields

- `GET /api/v1/egypt-locations/` returns public dropdown data for Egypt
  governorates and city/center codes.
- `governorate` stores a controlled Egypt governorate code.
- `city` stores a controlled Egypt city/center code and must belong to the
  selected governorate.
- `address` remains detailed free-text address content.

Frontend clients should select `governorate` first, then filter the city
dropdown from `/api/v1/egypt-locations/` before submitting Club create/update
requests.

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

## Sprint 6 Settlement Endpoints

- `GET /api/v1/clubs/{club_slug}/settlements/`
- `POST /api/v1/clubs/{club_slug}/settlements/`
- `GET /api/v1/clubs/{club_slug}/settlements/{id}/`
- `GET /api/v1/clubs/{club_slug}/settlements/preview/`
- `POST /api/v1/clubs/{club_slug}/settlements/{id}/mark-settled/`

Settlement preview summarizes unsettled transactions for a selected period
without creating records. Settlement creation snapshots matching unsettled
transactions into settlement lines. Mark-settled changes only `PENDING`
settlements to `SETTLED`.

Platform admins and owners can manage settlements. Managers can manage
settlements only when the club has `manager_can_settle_transactions=True`.
Staff cannot access settlement endpoints in Sprint 6.

Useful settlement list filters:

- `status`
- `court`
- `period_from`
- `period_to`
- `created_by`
- `settled_by`

## Sprint 7 Audit Log Endpoints

- `GET /api/v1/clubs/{club_slug}/audit-logs/`
- `GET /api/v1/clubs/{club_slug}/audit-logs/{id}/`

Audit logs are generated by important booking, transaction, and settlement
business actions. They are read-only through the API.

Platform admins, owners, and managers can view audit logs for the selected
club. Staff cannot access audit logs in Sprint 7.

Useful audit log filters:

- `action`
- `entity_type`
- `entity_id`
- `actor`
- `court`
- `date`
- `date_from`
- `date_to`

## Sprint 8 Dashboard and Availability Endpoints

- `GET /api/v1/clubs/{club_slug}/courts/{court_id}/availability/`
- `GET /api/v1/clubs/{club_slug}/calendar/`
- `GET /api/v1/clubs/{club_slug}/dashboard/overview/`
- `GET /api/v1/clubs/{club_slug}/dashboard/revenue/`
- `GET /api/v1/clubs/{club_slug}/dashboard/court-utilization/`

Availability returns generated slots for one court and date. `HOLD` and
`CONFIRMED` bookings block slots; terminal booking statuses do not.

Calendar returns frontend-friendly booking items with payment summary fields.
Staff can use availability and calendar only for their assigned court. Platform
admins, owners, and managers can access all selected-club courts.

Overview, revenue, and court-utilization endpoints are financial dashboard
summaries. Platform admins, owners, and managers can access them. Staff cannot.

Useful Sprint 8 query parameters:

- Availability: `date`
- Calendar: `date`, `date_from`, `date_to`, `court`, `status`
- Overview: `date_from`, `date_to`, `court`
- Revenue: `date_from`, `date_to`, `group_by`, `court`, `payment_method`
- Court utilization: `date_from`, `date_to`

## Demo Seed Data

Create richer local/demo data for Swagger or Postman testing:

```bash
python manage.py seed_demo_data
```

The command is idempotent and local/dev only. All demo users use password
`test-pass-123`.

Main users:

- `platform_admin`
- `owner_a`, `manager_a`, `staff_a`
- `owner_b`, `manager_b`, `staff_b`
- `owner_c`, `manager_c`, `staff_c`

Main clubs:

- `demo-football-club`: happy-path testing; managers can settle and change
  pricing.
- `demo-restricted-club`: restricted manager settlement and pricing flags.
- `demo-other-club`: cross-club scoping checks.

Token examples:

```json
{"username": "staff_a", "password": "test-pass-123"}
```

```json
{
  "username": "staff_a",
  "password": "test-pass-123",
  "club_slug": "demo-football-club"
}
```

The seed command includes role-specific memberships, two courts per club,
working hours, bookings across all statuses, transactions, pending and settled
settlements, unsettled transactions for preview testing, and audit log examples.
It supports Sprint 8 manual testing for blocked/free availability slots,
calendar scoping, dashboard summaries, revenue, utilization, and staff financial
dashboard denial.

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
