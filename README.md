# Sloty Backend

Sloty is a Django + Django REST Framework backend for a sports court rental
management system. The default local settings module is
`config.settings.local`.

## Documentation source of truth

1. `AGENTS.md` — current engineering architecture and conventions.
2. Locked contracts under `docs/`, currently
   `docs/recurring-bookings-contract.txt` and
   `docs/financial-consistency-contract.txt`.
3. This README — setup and API/operator guidance.
4. `docs/business-analysis.txt`, `docs/documentation.txt`, and
   `docs/sprints.txt` — historical planning context only.

Business calendar rules use `TIME_ZONE=Africa/Cairo` with timezone-aware UTC
storage (`USE_TZ=True`).

JWT access/refresh lifetimes are set with `JWT_ACCESS_TOKEN_MINUTES` (default
60) and `JWT_REFRESH_TOKEN_DAYS` (default 7). Token claims are unchanged. A
12-hour working shift is supported when the frontend silently refreshes;
production must not override refresh lifetime to an incompatible short value.
Do not set refresh lifetime to exactly 12 hours.

## Local Setup

Create a local environment file from the safe example values:

```bash
cp .env.example .env
```

Update `.env` for your machine. The default example uses SQLite; set
`DB_ENGINE=postgresql` and the `DB_*` values when using PostgreSQL.
PostgreSQL concurrency tests skip on SQLite; run
`DB_ENGINE=postgresql pytest tests/test_postgresql_concurrency.py` when a
PostgreSQL database is available.

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

Optional SQL request performance logs can be enabled without `DEBUG`:

```env
SQL_QUERY_STATS_ENABLED=true
SQL_QUERY_STATS_VERBOSE=false
SQL_QUERY_STATS_WARN_QUERY_COUNT=1
SQL_QUERY_STATS_SLOW_QUERY_MS=100
SQL_QUERY_STATS_SLOW_REQUEST_MS=500
SQL_QUERY_STATS_MAX_QUERY_SAMPLES=5
SQL_QUERY_STATS_MAX_SQL_LENGTH=500
```

When enabled, every API request logs a `[PERF]` summary with method, path,
status, query count, combined SQL time (`db=`), and total request time.
A `[PERF:WARN]` line is added when query count exceeds
`SQL_QUERY_STATS_WARN_QUERY_COUNT` (default `1`). That warning is an
observability threshold, not a required query budget. Duplicate SQL+params
and repeated SQL shapes (possible N+1, logged as `[PERF:N+1?]`) are
reported separately. Verbose mode may include truncated SQL structure
and never logs parameter values.

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

Expired access tokens return stable API code `SESSION_EXPIRED`. A stale token
for an inactive user returns `USER_INACTIVE`; a stale token for a deleted user
returns `USER_DELETED`. Frontend recovery logic should branch on `code`, not on
localized `message` text.
Club-scoped endpoints return `CLUB_ACCESS_REVOKED` with `details.club_slug`
when an authenticated user no longer has active access to that selected club.
Token obtain returns the same code when a valid optional `club_slug` is supplied
but the user has no active membership in that club.

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

Platform admins and club owners can list all memberships in the selected club.
Managers can list active MANAGER and STAFF employees. Staff cannot list club
users.

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
- `DELETE /api/v1/clubs/{club_slug}/memberships/{id}/` soft-deletes a
  membership. This is permanent removal from the current club membership
  experience and is distinct from `PATCH {"is_active": false}` (temporary
  deactivation that can be reversed). Soft-deleted memberships cannot be
  reactivated, disappear from current membership/user lists, and grant no
  club access. Recreating the same club, user, role, and court after delete
  is rejected. The user account and historical operational records remain.
  Deactivating or deleting an operational STAFF membership is rejected with
  `MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED` while that user's canonical Current
  Custody in the selected club is non-zero.
- `/api/v1/clubs/{club_slug}/courts/`
- `GET /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`
- `PUT /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`

Court working hours are court-scoped. The nested `working-hours` route returns
the selected court name, `pricing_configured`, and seven weekday rows. `PUT`
replaces the weekly schedule and pricing for that court:

```json
{
  "working_hours": [
    {
      "weekday": 0,
      "pricing_periods": [
        {
          "starts_at": "10:00:00",
          "ends_at": "18:00:00",
          "price": "200.00"
        },
        {
          "starts_at": "18:00:00",
          "ends_at": "23:00:00",
          "price": "300.00"
        }
      ]
    },
    {
      "weekday": 1,
      "pricing_periods": []
    }
  ]
}
```

`opens_at`, `closes_at`, and `is_closed` are not accepted. A weekday with no
pricing periods is closed. Operating hours come from the child pricing
periods.

The older `/api/v1/clubs/{club_slug}/court-working-hours/` row-level route is
kept temporarily for GET compatibility. POST/PATCH writes are rejected with
`WORKING_HOURS_USE_WEEKLY_ENDPOINT`; new clients must use the nested court
route so working hours and pricing remain atomic.

Court create/update requests no longer accept `default_price`. Court responses
include `pricing_configured`, `minimum_slot_price`, and `maximum_slot_price`.
`Court.default_price` remains in the database only as legacy migration data.
Every open working-hours row must have complete, non-overlapping pricing
coverage with boundaries aligned to `slot_duration_minutes`.

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
- `needs_action`
- `overdue`
- `has_remaining_amount`
- `ended`
- `hold_expiring`
- `search` (customer name, mobile, or notes; phone variants such as
  `01012345678`,
  spaced digits, and `+201012345678`)
- `upcoming=true` (`HOLD`/`CONFIRMED` and `end_time > now`, including
  in-progress bookings)

Booking list and detail include read-only `hold_expires_at`. For `HOLD` it is
`min(created + court.internal_hold_expiry_hours, booking.start_time)`. For
other statuses it is `null`.
Booking list rows also include `notes` (an empty string when unset), so list
cards do not need a Booking detail request.

Booking overlap validation treats `HOLD`, `CONFIRMED`, `COMPLETED`, and
`NO_SHOW` bookings as blocking historical or active slots. Only `CANCELLED` and
`EXPIRED` bookings release their time slot for a new booking.

The slots endpoint generates availability rows from a selected court's working
hours, pricing periods, and slot duration. It accepts `court` plus either
`date` or `date_from`/`date_to` query parameters. Each slot includes
`slot_price`, the current configured price for that specific slot. `FREE` and
`UNAVAILABLE` may appear as response-level slot states for UI display, but they
are not persisted booking statuses.
Starting a recurring booking can return `RECURRING_UNAVAILABLE` when the
selected base slot is free but the weekly recurrence pattern conflicts later.

Booking create accepts optional `client_request_id` as a UUID idempotency key.
Online clients may omit it, but offline/retry clients should send it. The key is
unique inside the selected club and is stored on the Booking row. Replaying the
same key with the same logical booking request returns the original Booking with
HTTP 200. Reusing the same key for a different logical request returns HTTP 409
with `BOOKING_CLIENT_REQUEST_MISMATCH`. Idempotency is retained for as long as
the Booking row exists.

New booking and reschedule prices are calculated from working-hour pricing
periods and stored in `Booking.total_price` as a historical snapshot. Existing
booking snapshots are not recalculated when court pricing changes. Booking
times must be inside working hours, fully priced, and aligned to the court slot
grid. Pricing errors use `BOOKING_OUTSIDE_WORKING_HOURS`,
`BOOKING_PRICE_NOT_CONFIGURED`, `BOOKING_MULTIDAY_NOT_SUPPORTED`, and
`BOOKING_TIME_NOT_ALIGNED_WITH_SLOT_GRID`.

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
- `settlement` (exact settlement id; still scoped)
- `search` (booking customer name/phone variants and payment reference)
- `ordering` (`-created` newest, `created` oldest; id is the tie-breaker)

Transaction list and detail include `booking_customer_name`,
`booking_customer_phone`, `booking_start_time`, and `booking_end_time`.

Creating the first valid transaction for a `HOLD` booking confirms it.
Transactions are immutable financial history: PATCH, PUT, and DELETE are not
available. To correct an entry, cancel the original transaction with a reason,
then create the corrected transaction through the normal transaction create
endpoint:

```json
{"reason": "Wrong amount entered"}
```

Staff transaction lists are collector-scoped ("my collections"): Staff see
only transactions they recorded (`created_by` is the current user) on their
assigned court. Query parameters cannot expand that to another collector.
Owners, managers, and platform admins keep club/court-wide lists.

Platform admins may cancel any eligible transaction in the selected club. Owners,
managers, and staff may cancel only transactions they created and can access;
staff remain limited to their assigned court and to their own collections.
Already cancelled or settled transactions and transactions attached to terminal
bookings cannot be cancelled.

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
- `POST /api/v1/clubs/{club_slug}/bookings/{id}/end-recurrence/`
- `GET /api/v1/clubs/{club_slug}/bookings/{id}/recurrence-next/`

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

For an ACTIVE recurring CONFIRMED booking, call
`GET /api/v1/clubs/{club_slug}/bookings/{id}/recurrence-next/` before
completion to show the next occurrence datetime, slot price, required deposit,
and whether a digital payment reference will be required
(`requires_digital_payment_reference`; `requires_payment_reference` is a
deprecated alias). The preview does not write data. Completing with
`continue_recurring=true` revalidates the same rules. Clients must not send
next amounts.

`expire` accepts an empty body and is allowed only for `HOLD` bookings.

To expire due HOLD bookings, run:

```bash
python manage.py expire_hold_bookings
```

The command uses each court's effective hold deadline
`min(created + internal_hold_expiry_hours, booking.start_time)` and is
idempotent.
There is no Celery worker. This repository does not ship a cron, systemd
timer, or other scheduler unit. Production and staging must schedule this
command (cron or equivalent, typically every 5 minutes) or HOLD bookings
will not expire automatically. The product UI must not promise automatic
cancel unless that job is actually running.

## Sprint 6 Settlement Endpoints

- `GET /api/v1/clubs/{club_slug}/settlements/`
- `POST /api/v1/clubs/{club_slug}/settlements/`
- `GET /api/v1/clubs/{club_slug}/settlements/{id}/`
- `GET /api/v1/clubs/{club_slug}/settlements/preview/`
- `GET /api/v1/clubs/{club_slug}/settlements/unsettled-summary/`
- `POST /api/v1/clubs/{club_slug}/settlements/{id}/mark-settled/`

Settlement is user-based. The owner/admin/allowed manager selects a collector
from the club users list, previews that user's open balance, then approves a
settlement for all currently unsettled valid transactions recorded by that user
in the selected club.

`GET .../settlements/unsettled-summary/` is the management all-collector view of
current unsettled custody (not persisted Settlement rows). It uses the same
candidate definition as preview. `period_start` is the earliest unsettled
transaction for that collector; `period_end` is request time. Staff cannot use
this endpoint. Managers need `manager_can_settle_transactions=true`, do not see
OWNER money, and cannot approve their own row. Optional `collected_by` and
`court` follow preview access rules. POST create remains the receive-money
mutation. GET list remains settlement history.

Current Custody is all-time state: every authorized, non-cancelled signed
Transaction with no settlement line. It is not restricted by Transaction age,
Booking date, dashboard/report ranges, payment method, or an analytical
settlement-status filter. The default covers all Courts accessible to the actor;
`court` narrows it only when explicitly supplied. Main totals include all
payment methods, while `totals_by_payment_method` gives signed per-method nets.
Zero-net and negative-net collectors remain visible when candidate rows exist.

Settlement list/detail include `court_name` (`null` when `court` is omitted)
and `settled_by_name` (full name if present, otherwise username).
Settlement lines and preview transactions include booking customer name/phone
and booking start/end times.

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
settlements only when their club membership has
`manager_can_settle_transactions=True`. Staff cannot access settlement
endpoints in Sprint 6.

Useful settlement list filters:

- `status`
- `court`
- `period_from`
- `period_to`
- `collected_by`
- `created_by`
- `settled_by`

Settlement preview/create include ordinary signed booking transactions only.
Net amount is booking payments plus signed booking refunds.

## Booking Recurrence

Create a weekly recurrence through the normal booking endpoint with
`is_recurring=true`. The response uses `source=RECURRING` and
`recurrence_status=ACTIVE`; no future booking rows are generated.

Future schedule slots blocked only by an active weekly recurrence return
`slot_status=RECURRING_RESERVED`, `booking=null`, the active anchor booking
id, and `recurring_context` (anchor id, customer name/phone, recurrence
status). Selected slot date/time/`slot_price` describe the future occurrence;
anchor lifecycle and financial state are not copied onto the virtual slot.
Completing an active recurring booking requires a `continue_recurring`
decision; continuation creates next week's booking and records any required
deposit as a normal booking payment transaction.

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

List and detail responses also include `actor_name`, `court_name`, source labels
for those names, and an entity-specific `summary`. New events preserve display
facts at event time in existing Audit JSON, so later user/Court/customer changes
do not rewrite history. Older rows are not backfilled; current actor/Court
relations may be used only as explicitly labeled display fallbacks. No per-row
Booking, Transaction, or Settlement lookup is needed.

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

- `GET /api/v1/public/clubs/{club_slug}/courts/{court_id}/availability/`
- `GET /api/v1/clubs/{club_slug}/courts/{court_id}/availability/`
- `GET /api/v1/clubs/{club_slug}/calendar/`
- `GET /api/v1/clubs/{club_slug}/dashboard/overview/`
- `GET /api/v1/clubs/{club_slug}/dashboard/summary/`
- `GET /api/v1/clubs/{club_slug}/dashboard/revenue/`
- `GET /api/v1/clubs/{club_slug}/dashboard/court-utilization/`

Availability returns generated slots for one court and date. `HOLD`,
`CONFIRMED`, `COMPLETED`, and `NO_SHOW` bookings block slots. Only `CANCELLED`
and `EXPIRED` bookings release their slots.

Public availability is deliberately sanitized and anonymous. It exposes only
public club/court identity, the requested date/window, and per-slot
`AVAILABLE`/`UNAVAILABLE` state. It does not expose booking IDs, customer data,
notes, staff, payment/transaction data, recurrence context, or internal booking
statuses.

Calendar returns frontend-friendly booking items with payment summary fields.
Staff can use availability and calendar only for their assigned court. Platform
admins, owners, and managers can access all selected-club courts.

Summary returns compact operational booking counts, scoped court metadata, and
financial totals for the selected club and date/range. Platform admins and
owners see all selected-club courts. Managers see all selected-club courts under
the current club-level manager architecture. Staff can access summary for their
assigned court only and receive operational counts with
`financial_visible=false`; financial fields are returned as `null`.

Dashboard Transaction activity cards are period-scoped to the selected
summary/overview date range. Dashboard unsettled/current-custody cards are
all-time state, so old unsettled money is not hidden just because today's date
is selected. They respect explicit authorized `court` and `collected_by`
filters. `payment_method` and `settlement_status` filter period activity only;
they never redefine Current Custody.

- `unsettled_transaction_count`: number of eligible unsettled transactions.
- `unsettled_transaction_total_amount`: total money amount of those eligible
  unsettled transactions.
- `staff_with_unsettled_transactions_count`: number of distinct users who
  currently have eligible unsettled transactions.

Eligible unsettled transactions are non-cancelled, signed transactions
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
- `staff_unsettled_money`: grouped current signed balance by collector across
  all accessible Courts by default, or for one Court when explicitly filtered.

Completed bookings with remaining amount are not included in normal
`needs_action_count`; they are treated as data integrity warnings. EXPIRED
bookings in the selected dashboard date range are included because that
range is explicit operational date context. Unscoped booking
`needs_action=true` does not include historical EXPIRED rows. Hold expiry
uses each court's `internal_hold_expiry_hours` capped by `start_time`;
`hold_expiring=true` uses a 30-minute warning window before the calculated
expiry time.

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
- `has_remaining_amount=true&status=CONFIRMED&ended=true`
- `hold_expiring=true`

`has_remaining_amount` is the canonical remaining-amount filter (`true` means
paid amount is less than `total_price`). Deprecated `remaining_amount_gt`
still returns CONFIRMED bookings with remaining amount greater than zero when
the parameter is present.

Completion requires remaining amount already paid.
`confirm_collect_remaining_cash` is accepted as a deprecated no-op and does
not create a cash transaction.

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
filtered by transaction creation date. Peak and low-demand rows use fixed
60-minute clock buckets, so zero-demand working hours can appear in low-demand
results. Multi-day booking financials appear only on the booking's local start
date in `usage_by_day`; null creators are labeled as localized `Unknown`.

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

Create the scoped Albaladya frontend/local testing scenario:

```bash
python manage.py seed_demo_data --scenario albaladya-test
```

This creates or repairs one club (`albaladya-test`), one court, all seven
working-hour rows, and these development/test-only users. Never use these
credentials in production:

- `admin` / `Admin@123456`
- `owner` / `Admin@123456`
- `manager` / `Admin@123456`
- `staff` / `Admin@123456`

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
