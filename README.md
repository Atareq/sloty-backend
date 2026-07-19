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

## Club Users Endpoint

- `GET /api/v1/clubs/{club_slug}/users/`

This read-only endpoint returns users through their `ClubMembership` rows,
including identity fields, role, court assignment, and membership active state.
It does not create or update users.

Useful filters:

- `role`
- `court`
- `is_active`
- `search`

Platform admins and club owners can list memberships in the selected club.
Managers and staff cannot list club users.

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
- `GET /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`
- `PUT /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`

Court working hours are court-scoped. The nested `working-hours` route returns
the selected court name and seven weekday rows. `PUT` replaces the weekly
schedule for that court:

```json
{
  "working_hours": [
    {
      "weekday": 0,
      "opens_at": "10:00:00",
      "closes_at": "23:00:00",
      "is_closed": false
    },
    {
      "weekday": 1,
      "opens_at": null,
      "closes_at": null,
      "is_closed": true
    }
  ]
}
```

The older `/api/v1/clubs/{club_slug}/court-working-hours/` row-level route is
kept temporarily for compatibility. New clients should use the nested court
route.

## Sprint 3 Booking Endpoints

- `/api/v1/clubs/{club_slug}/bookings/`

Useful booking list filters:

- `court`
- `status`
- `source`
- `date`
- `date_from`
- `date_to`

## Transaction Endpoints

- `/api/v1/clubs/{club_slug}/transactions/`
- `/api/v1/clubs/{club_slug}/transactions/{id}/`
- `POST /api/v1/clubs/{club_slug}/transactions/{id}/cancel/`

Useful transaction list filters:

- `booking`
- `court`
- `payment_method`
- `date`
- `date_from`
- `date_to`
- `created_by`
- `is_cancelled`

Creating the first valid transaction for a `HOLD` booking confirms it.
Transactions are immutable financial history: PATCH, PUT, and DELETE are not
available. To correct an entry, cancel the original transaction with a reason,
then create the corrected transaction through the normal transaction create
endpoint:

```json
{"reason": "Wrong amount entered"}
```

Platform admins may cancel any eligible transaction in the selected club. Owners,
managers, and staff may cancel only transactions they created and can access;
staff remain limited to their assigned court. Already cancelled or settled
transactions and transactions attached to terminal bookings cannot be cancelled.

Cancelled transactions remain visible in list/detail responses and can be selected
with `?is_cancelled=true` or `?is_cancelled=false`. They do not count toward booking
paid/remaining amounts, completion collection, settlement preview/creation,
calendar payment summaries, or dashboard revenue. If cancelling removes the last
valid payment from a `CONFIRMED` booking, the booking returns to `HOLD`.

Duplicate non-blank payment references are rejected within the same club; blank
references are allowed. Refunds, reversals, and online payment gateway
integration are not implemented.

## Sprint 5 Booking Lifecycle Endpoints

- `POST /api/v1/clubs/{club_slug}/bookings/{id}/cancel/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/complete/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/no-show/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/reschedule/`
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/expire/`

Allowed transitions are `HOLD -> CANCELLED`, `HOLD -> EXPIRED`,
`CONFIRMED -> CANCELLED`, `CONFIRMED -> COMPLETED`, and
`CONFIRMED -> NO_SHOW`. Terminal statuses remain locked.

Lifecycle request bodies:

```json
{"reason": "Customer cancelled"}
```

`cancel` accepts an optional reason for platform admins, owners, and managers.
Court staff must provide a non-empty cancellation reason.

```json
{"reason": "Customer did not arrive"}
```

`no-show` accepts an optional reason and is allowed only for `CONFIRMED`
bookings.

```json
{
  "court": 1,
  "start_time": "2026-07-20T20:00:00Z",
  "end_time": "2026-07-20T21:00:00Z",
  "reason": "Customer changed time"
}
```

`reschedule` is allowed for `HOLD` and `CONFIRMED` bookings only. The new court
must belong to the selected club and be accessible to the actor. The new slot
must not overlap another active booking. Transactions stay attached to the same
booking. If the recalculated price is higher, `total_price` increases; if it is
lower or equal, the existing `total_price` remains.

```json
{"confirm_collect_remaining_cash": true}
```

`complete` is allowed only for `CONFIRMED` bookings. If the booking has a
remaining amount, the request must explicitly confirm cash collection with
`confirm_collect_remaining_cash=true`; the backend then creates a CASH
transaction for the remaining amount before marking the booking completed. Fully
paid bookings may be completed with an empty body.

`expire` accepts an empty body and is allowed only for `HOLD` bookings.

To expire due HOLD bookings manually, run:

```bash
python manage.py expire_hold_bookings
```

The command uses each court's `internal_hold_expiry_hours`, is idempotent, and
does not require Celery or a scheduler.

## Sprint 6 Settlement Endpoints

- `GET /api/v1/clubs/{club_slug}/settlements/`
- `POST /api/v1/clubs/{club_slug}/settlements/`
- `GET /api/v1/clubs/{club_slug}/settlements/{id}/`
- `GET /api/v1/clubs/{club_slug}/settlements/preview/`
- `POST /api/v1/clubs/{club_slug}/settlements/{id}/mark-settled/`

Settlement is user-based. The owner/admin/allowed manager selects a collector
from the club users list, previews that user's open balance, then creates a
settlement for all currently unsettled valid transactions recorded by that user
in the selected club.

Preview:

```text
GET /api/v1/clubs/{club_slug}/settlements/preview/?collected_by={user_id}
```

Create:

```json
{
  "collected_by": 15,
  "notes": "End of shift settlement"
}
```

No date range is required for preview or creation. `period_start` is computed
from the earliest selected transaction and `period_end` is the creation time.
Cancelled transactions are excluded, and settled transactions cannot be included
again. Mark-settled changes only `PENDING` settlements to `SETTLED`.

Platform admins and owners can manage settlements. Managers can manage
settlements only when the club has `manager_can_settle_transactions=True`.
Staff cannot access settlement endpoints in Sprint 6.

Useful settlement list filters:

- `status`
- `court`
- `period_from`
- `period_to`
- `collected_by`
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
- `GET /api/v1/clubs/{club_slug}/dashboard/summary/`
- `GET /api/v1/clubs/{club_slug}/dashboard/revenue/`
- `GET /api/v1/clubs/{club_slug}/dashboard/court-utilization/`

Availability returns generated slots for one court and date. `HOLD` and
`CONFIRMED` bookings block slots; terminal booking statuses do not.

Calendar returns frontend-friendly booking items with payment summary fields.
Staff can use availability and calendar only for their assigned court. Platform
admins, owners, and managers can access all selected-club courts.

Summary returns compact operational booking counts, scoped court metadata, and
financial totals for the selected club and date/range. Platform admins and
owners see all selected-club courts. Managers see all selected-club courts under
the current club-level manager architecture. Staff can access summary for their
assigned court only and receive operational counts with
`financial_visible=false`; financial fields are returned as `null`.

Summary response shape:

- `club`: selected club id, slug, and name.
- `scope`: role, optional filtered court, visible court ids, and
  `financial_visible`.
- `period`: `date_from` and `date_to`.
- `summary`: court counts, booking status counts, booking value/paid/remaining,
  transaction totals, unsettled/settled transaction totals, and
  pending/settled settlement totals.
- `courts`: per-court booking counts plus court-level booking and transaction
  totals.

Overview, revenue, and court-utilization endpoints are financial dashboard
summaries. Platform admins, owners, and managers can access them. Staff cannot.

Useful Sprint 8 query parameters:

- Availability: `date`
- Calendar: `date`, `date_from`, `date_to`, `court`, `status`
- Overview: `date_from`, `date_to`, `court`
- Summary: `date`, `date_from`, `date_to`, `court`
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
