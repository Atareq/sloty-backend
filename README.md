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

Optional local SQL request summaries can be enabled in `.env`:

```env
SQL_QUERY_STATS_ENABLED=true
SQL_QUERY_STATS_VERBOSE=false
SQL_QUERY_STATS_SLOW_QUERY_MS=100
```

When enabled, API requests log one terminal line with method, path, status,
query count, combined SQL time, and total request time.

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
  flag, account creator, and active club memberships for frontend club
  selection.
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

`/api/v1/me/` always includes `account_created_by`. The value is a nested
`{"id": ..., "name": ...}` object from `User.created_by`, or `null` for
historical/system-created accounts.

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
- `GET /api/v1/clubs/{club_slug}/bookings/slots/`

Useful booking list filters:

- `court`
- `status`
- `source`
- `date`
- `date_from`
- `date_to`

Booking overlap validation treats `HOLD`, `CONFIRMED`, `COMPLETED`, and
`NO_SHOW` bookings as blocking historical or active slots. Only `CANCELLED` and
`EXPIRED` bookings release their time slot for a new booking.

The slots endpoint generates availability rows from a selected court's working
hours and slot duration. It accepts `court` plus either `date` or
`date_from`/`date_to` query parameters. `FREE` may appear as a response-level
slot state for UI display, but it is not a persisted booking status.

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
- `settlement_status` (`unsettled` or `settled`)

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
paid/remaining amounts, completion payment checks, settlement preview/creation,
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
must not overlap another blocking booking. Transactions stay attached to the
same booking. If the recalculated price is higher, `total_price` increases; if
it is lower or equal, the existing `total_price` remains.

`complete` is allowed only for `CONFIRMED` bookings. The booking must be fully
paid before completion. If a remaining amount exists, the backend returns 409
with `BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT`; record the missing payment as a
normal transaction first, then complete the booking.

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
from the club users list, previews that user's open balance, then approves a
settlement for all currently unsettled valid transactions recorded by that user
in the selected club.

Preview:

```text
GET /api/v1/clubs/{club_slug}/settlements/preview/?collected_by={user_id}
GET /api/v1/clubs/{club_slug}/settlements/preview/?collected_by={user_id}&court={court_id}
```

Preview is read-only. It creates no settlement rows, settlement lines, audit
rows, locks, or transaction state changes.

Approve:

```json
{
  "collected_by": 15,
  "court": 3,
  "notes": "End of shift settlement"
}
```

`court` is optional. When omitted, approval includes all currently unsettled
valid transactions for the selected collector inside the actor's allowed court
scope. No date range is required for preview or approval. `period_start` is
computed from the earliest selected transaction and `period_end` is the approval
time. Cancelled transactions are excluded, and settled transactions cannot be
included again. API approval creates settlements directly as `SETTLED` with
`collected_by` set to the collector and `created_by`, `settled_by`, and
`settled_at` set to the approving actor.
Mark-settled remains for legacy `PENDING` settlements and rejects already
`SETTLED` settlements.

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
Responses include stable machine action values for filtering and logic plus a
localized display label for the UI:

```json
{
  "action": "TRANSACTION_CANCELLED",
  "action_label": "Transaction cancelled"
}
```

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

Availability returns generated slots for one court and date. `HOLD`,
`CONFIRMED`, `COMPLETED`, and `NO_SHOW` bookings block slots. Only `CANCELLED`
and `EXPIRED` bookings release their slots.

Calendar returns frontend-friendly booking items with payment summary fields.
Staff can use availability and calendar only for their assigned court. Platform
admins, owners, and managers can access all selected-club courts.

Summary returns compact operational booking counts, scoped court metadata, and
financial totals for the selected club and date/range. Platform admins and
owners see all selected-club courts. Managers see all selected-club courts under
the current club-level manager architecture. Staff can access summary for their
assigned court only and receive operational counts with
`financial_visible=false`; financial fields are returned as `null`.

Dashboard transaction cards are period-scoped to the selected summary/overview
date range. Dashboard unsettled money cards represent current open balance by
default, so old unsettled money is not hidden just because today's date is
selected. They still respect optional `court`, `collected_by`, and
`payment_method` filters.

- `unsettled_transaction_count`: number of eligible unsettled transactions.
- `unsettled_transaction_total_amount`: total money amount of those eligible
  unsettled transactions.
- `staff_with_unsettled_transactions_count`: number of distinct users who
  currently have eligible unsettled transactions.

Eligible unsettled transactions are non-cancelled, positive-amount transactions
inside the selected club/court access scope with no settlement line. Dashboard
settlement-related metrics are derived from unsettled transactions and their
`created_by` users, not from `Settlement.status=PENDING`. The previous
`unsettled_transaction_amount`, `pending_settlement_user_count`,
`pending_settlement_count`, and `pending_settlement_amount` response fields are
removed from dashboard summary and overview responses.

Summary also returns:

- `needs_action_breakdown`: hold waiting payment, overdue confirmed, remaining
  after slot end, and expiring hold counts.
- `payment_method_totals`: grouped period transaction totals.
- `staff_unsettled_money`: grouped current open balance by collector and court.

Completed bookings with remaining amount are not included in normal
`needs_action_count`; they are treated as data integrity warnings. Hold expiry
uses each court's `internal_hold_expiry_hours`; `hold_expiring=true` uses a
30-minute warning window before the calculated expiry time.

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
- Summary: `date`, `date_from`, `date_to`, `court`, `collected_by`,
  `payment_method`, `settlement_status`
- Revenue: `date_from`, `date_to`, `group_by`, `court`, `payment_method`
- Court utilization: `date_from`, `date_to`

Useful booking list filters for dashboard cards:

- `needs_action=true`
- `overdue=true`
- `remaining_amount_gt=0&ended=true`
- `hold_expiring=true`

## Reports Endpoints

- `GET /api/v1/clubs/{club_slug}/reports/court-usage/`

Court usage reporting is a read-only analytics endpoint owned by
`apps/reports/`. It is separate from compact dashboard endpoints and does not
change existing dashboard response contracts.

Required filters:

- `date_from`
- `date_to`

Optional filters:

- `court`
- `period` (`all_day`, `daytime`, `evening`, or `custom`)
- `hour_from` and `hour_to` when `period=custom`
- `staff`
- `status` (`HOLD`, `CONFIRMED`, `COMPLETED`, or `NO_SHOW`)

The date range is inclusive from the client perspective and limited to 31
calendar days. Default usage includes `CONFIRMED`, `COMPLETED`, and `NO_SHOW`.
`HOLD` is included only when requested explicitly. `CANCELLED` and `EXPIRED`
are not accepted for this usage report.

Stable validation codes include `REPORT_DATE_RANGE_INVALID`,
`REPORT_DATE_RANGE_TOO_LARGE`, `CUSTOM_REPORT_HOURS_REQUIRED`,
`INVALID_CUSTOM_REPORT_HOURS`, `REPORT_STAFF_NOT_IN_CLUB`, and
`INVALID_COURT_USAGE_STATUS`.

Financial totals are selected by booking time: matching bookings are found by
overlap with the selected report scope, then the endpoint sums each booking's
full `total_price` and non-cancelled attached transactions. Payments are not
filtered by transaction creation date. Peak and low-demand rows use generated
60-minute working-hour buckets, so zero-demand working hours can appear in
low-demand results.

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
