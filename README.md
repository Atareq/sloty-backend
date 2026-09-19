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

## Runtime Version

Authenticated `GET /api/system/version/` returns the backend release running on
this host:

```json
{
  "service": "sloty-backend",
  "release": "v0.7.0",
  "commit": "91bd3aa28e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b"
}
```

- `release` is the human-readable deployment tag (`v<major>.<minor>.<patch>`).
- `commit` is the exact deployed Git SHA and the authoritative technical identity.
- The endpoint reads `/etc/sloty/backend-release.json`. It does not query GitHub,
  `master`, `.git`, or the database.
- Local development and tests usually have no metadata file; the response stays
  HTTP 200 with `"release": null` and `"commit": null`.
- Production writes that file only after a successful deploy and
  `GET http://127.0.0.1:8020/health/` (`Host: api.sloty.baronlearning.com`),
  using an atomic rename. A failed deploy leaves the previous file in place.
- Responses use `Cache-Control: no-store`.

Keep `scripts/deploy-sloty-backend` as the canonical production executable.
CI SSHs to the VPS and runs that file from the git worktree:

```bash
sudo /srv/sloty/backend/scripts/deploy-sloty-backend <SHA> <TAG>
```

There is no `/usr/local/sbin` copy. The script discovers the repository root
from git, snapshots its own `scripts/` bundle to `/tmp` before checkout, uses
`<repo>/.venv` (`Python 3.12.14` from `/opt/sloty-runtime/python/`), loads
`/etc/sloty/env/prod.env`, restarts only `sloty-backend.service`, and must not
manage PostgreSQL, Nginx, or system Python. The canonical unit is
`deploy/systemd/sloty-backend.service`; it loads only `prod.env`, never the
backup credentials. CI calculates the release tag once, deploys that exact SHA
and tag, then creates or reuses the GitHub Release with the same values.

### Production Environment Files

The repository tracks placeholder-only templates in `env/`. Django production
configuration is defined by `config/settings/base.py`: `SECRET_KEY`, `DEBUG`,
`ALLOWED_HOSTS`, PostgreSQL `DB_*` settings, optional `DB_SSLMODE`, and the
optional JWT and SQL-query-stat settings. No email-specific environment
variables are currently consumed by this backend. Backup-only R2 settings are
listed separately in `env/backup.env.example` and are not loaded by Gunicorn.
A future root-run backup service may load both files; no backup service is
implemented by this change.

On each trusted operator machine, secure local copies may use the same paths as
the VPS. These files are private and are never part of Git:

```text
/etc/sloty/env/prod.env
/etc/sloty/env/backup.env
```

To migrate the VPS without restarting against a missing env file, run the
following in order. The `--no-clobber` copy preserves an already prepared new
production file when retrying. Keep the old file until health verification
passes.

```bash
sudo install -d -o root -g sloty -m 0750 /etc/sloty/env
sudo cp -a /etc/sloty/backend.env /root/sloty-backend.env.before-env-migration
sudo cp --no-clobber /etc/sloty/backend.env /etc/sloty/env/prod.env
sudo chown root:sloty /etc/sloty/env/prod.env
sudo chmod 0640 /etc/sloty/env/prod.env
sudo touch /etc/sloty/env/backup.env
sudo chown root:root /etc/sloty/env/backup.env
sudo chmod 0600 /etc/sloty/env/backup.env
sudoedit /etc/sloty/env/backup.env
sudo cp -a "$(systemctl show -p FragmentPath --value sloty-backend.service)" /root/sloty-backend.service.before-env-migration
sudo install -o root -g root -m 0644 /srv/sloty/backend/deploy/systemd/sloty-backend.service /etc/systemd/system/sloty-backend.service
sudo systemctl daemon-reload
sudo systemctl restart sloty-backend.service
sudo systemctl is-active --quiet sloty-backend.service
curl --fail --silent --show-error --header 'Host: api.sloty.baronlearning.com' http://127.0.0.1:8020/health/
sudo rm /etc/sloty/backend.env
```

If verification fails, restore the old path and the saved unit, then reload
and restart:

```bash
sudo cp -a /root/sloty-backend.env.before-env-migration /etc/sloty/backend.env
sudo cp -a /root/sloty-backend.service.before-env-migration /etc/systemd/system/sloty-backend.service
sudo systemctl daemon-reload
sudo systemctl restart sloty-backend.service
sudo systemctl is-active --quiet sloty-backend.service
```

### PostgreSQL Backups and Restore

The backup and restore commands are `scripts/backup-sloty-db` and
`scripts/restore-sloty-db`. The host installer is
`scripts/install-host-dependencies`; it installs `rclone` and the PostgreSQL 17
client package when needed, then validates `pg_dump` and `pg_restore`. These
are host tools, not Python requirements. The installer does not restart
services.

Prepare a private local directory and install the repo-owned systemd units as
symlinks. Keep `/etc/sloty/env/backup.env` root-only (`root:root`, mode `0600`);
systemd reads both environment files before starting the backup as `sloty`.
The backend service itself does not load `backup.env`.

```bash
sudo /srv/sloty/backend/scripts/install-host-dependencies
sudo install -d -o sloty -g sloty -m 0700 /var/backups/sloty/postgres
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-db-backup.service /etc/systemd/system/sloty-db-backup.service
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-db-backup.timer /etc/systemd/system/sloty-db-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now sloty-db-backup.timer
```

The timer runs once daily at 09:00 `Africa/Cairo`, independent of the host
timezone, and catches up once after downtime. The service writes a PostgreSQL
custom archive compressed with Zstandard level 9, validates it with
`pg_restore --list`, uploads only to `sloty-prod-backups/db/`, and compares the
listed remote filename and size with the local archive before deleting it.
Rclone uses environment-backed Cloudflare S3 settings and `/dev/null` as its
config path; it does not create a persistent `rclone.conf`. Its S3 `no_head`
setting skips the post-PUT HEAD request that R2 rejected; the script performs
its own list-based size/name check afterward. See the
[PostgreSQL 17 `pg_dump` options](https://www.postgresql.org/docs/17/app-pgdump.html)
and [rclone S3 options](https://rclone.org/s3/). No ACL header is forced.

Successful uploads remove their local temporary archive. Failed uploads or
remote verification leave a valid local dump for recovery; dumps older than
seven days are pruned at the next run. R2 lifecycle retention remains configured
on the bucket, not by this script.

To run the first backup and inspect its result without showing environment
contents:

```bash
sudo systemctl start sloty-db-backup.service
sudo systemctl --no-pager --full status sloty-db-backup.service
sudo journalctl -u sloty-db-backup.service --since today --no-pager
```

For a restore test, first create an empty `sloty_restore_test` database owned
by or accessible to the configured DB role. Replace the sample object name with
one listed under the R2 `db/` prefix. `systemd-run` supplies the same env files
without shell-parsing them; the restore command validates the archive before
restoring and will not drop or clean the target.

```bash
sudo systemd-run --wait --pipe --collect --unit=sloty-db-restore-test \
  --property=User=sloty \
  --property=EnvironmentFile=/etc/sloty/env/prod.env \
  --property=EnvironmentFile=/etc/sloty/env/backup.env \
  /srv/sloty/backend/scripts/restore-sloty-db \
  --database sloty_restore_test \
  --remote 'sloty_prod-YYYY-MM-DDTHHMMSS+0300.dump'
```

Restoring to `sloty_prod` additionally requires
`--confirm-production sloty_prod`. Do not use that confirmation for a test
restore. To disable and remove only the scheduled backup units while preserving
local and R2 archives:

```bash
sudo systemctl disable --now sloty-db-backup.timer
sudo systemctl stop sloty-db-backup.service
sudo rm /etc/systemd/system/sloty-db-backup.timer /etc/systemd/system/sloty-db-backup.service
sudo systemctl daemon-reload
```

Unauthenticated `GET /health/` is the dedicated deploy/systemd probe and returns
`{"status": "ok"}` without touching PostgreSQL.

## Authentication Endpoints

- `POST /api/v1/auth/token/` obtains JWT access and refresh tokens.
- `POST /api/v1/auth/token/refresh/` refreshes an access token.
- `GET /api/v1/me/` returns the authenticated user's identity, `profile_role`,
  and account creator. Club scope is resolved from Profile extensions, not
  membership rows.
- `GET /api/v1/users/` manages base user accounts for platform admin users.
- `POST /api/v1/users/` creates platform admin users only.

Login is global and does not require a club slug. After login, clients call
`/api/v1/me/` and then club-scoped endpoints with a club slug the current
Profile can access.

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

Business APIs verify club access from the current Profile through the
Authorization Spine; clients must not treat JWT club/court claims as authority.

`/api/v1/me/` always includes `account_created_by`. The value is a nested
`{"id": ..., "name": ...}` object from `Profile.created_by`, or `null` for
historical/system-created profiles.

## Club User Onboarding

Platform admins can be created through `/api/v1/users/`, which always creates
an `ADMIN` Profile. Club owners and staff are Profile extensions
(`OwnerProfile.clubs`, `StaffProfile.club` + `StaffCourtAccess`). There is no membership-management
API and no Manager role.

OWNER, STAFF, and ADMIN live on `Profile.role`. STAFF requires an assigned court.

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

Booking attempts are persisted separately from operational bookings for
authenticated booking create requests that reach domain processing. Attempts
preserve the original request (`client_request_id`, customer fields, requested
time, `requested_at`, source, recurrence intent, actor, club, and court), the
backend outcome, failure code/details for rejected attempts, staff resolution,
and an optional resolved Booking link. Rejected attempts do not reserve slots,
appear in booking lists, affect availability, or enter financial totals.
`requested_at` is the staff/business capture time and must include timezone
information when supplied.

Minimal traceability endpoints:

- `GET /api/v1/clubs/{club_slug}/booking-attempts/`
- `GET /api/v1/clubs/{club_slug}/booking-attempts/{id}/`
- `POST /api/v1/clubs/{club_slug}/booking-attempts/{id}/dismiss/`

Useful booking-attempt filters are `court`, `attempted_by`, `outcome`,
`resolution`, `requested_source`, `status` (`ACCEPTED`, `REJECTED`, or
`DISMISSED`), `date`, `date_from`, and `date_to`. Staff see only their own
attempts on their assigned court. Owners, managers, and platform admins see the
attempts on their assigned court. Owners and platform admins see the
selected club scope. Dismissal is staff resolution of an unsuccessful attempt,
not Booking cancellation, and does not change Booking, Transaction, Settlement,
availability, or Current Custody state.

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
- `/api/v1/clubs/{club_slug}/transaction-attempts/`
- `/api/v1/clubs/{club_slug}/transaction-attempts/{id}/`
- `POST /api/v1/clubs/{club_slug}/transaction-attempts/{id}/dismiss/`

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

Payment sync clients may send optional `client_request_id` and timezone-aware
`occurred_at`. `client_request_id` is idempotent inside a club: the same logical
request returns the existing transaction, while a different request with the
same key returns `TRANSACTION_CLIENT_REQUEST_MISMATCH`.

Staff transaction lists are collector-scoped ("my collections"): Staff see
only transactions they recorded (`created_by` is the current user) on their
assigned court. Query parameters cannot expand that to another collector.
Owners, managers, and platform admins keep club/court-wide lists.
Owners and platform admins keep club/court-wide lists.

Platform admins may cancel any eligible transaction in the selected club. Owners,
managers, and staff may cancel only transactions they created and can access;
Platform admins may cancel any eligible transaction in the selected club. Owners
and staff may cancel only transactions they created and can access;
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

Transaction attempts are persisted separately from financial transactions for
offline/PWA traceability. A successful attempt links to the resulting
`Transaction`; a rejected attempt has no transaction and stores a machine
failure code/details. Rejected or dismissed attempts do not count toward
booking paid/remaining amounts, Current Custody, settlement preview/creation,
dashboard revenue, or transaction lists.

Useful transaction-attempt filters are `booking`, `court`, `attempted_by`,
`payment_method`, `outcome`, `resolution`, `failure_code`, computed `status`
(`ACCEPTED`, `REJECTED`, `DISMISSED`), `date`, `date_from`, and `date_to`.
Attempt date filters use `occurred_at`. Staff see only their own attempts on
their assigned court. Owners, managers, and platform admins see the selected
their assigned court. Owners and platform admins see the selected
club scope. Dismissal is staff resolution of an unsuccessful attempt, not
transaction cancellation, refund, reversal, settlement, or booking payment.

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
`cancel` accepts an optional reason for platform admins and owners.
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
next amounts. If an offline/historical recurrence anchor has missed several
weeks, continuation creates only the next current or future weekly occurrence;
missed past occurrences are not backfilled.

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
Settlement is user-based. The owner or platform admin selects a collector
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
this endpoint. Optional `collected_by` and `court` follow preview access rules.
POST create remains the receive-money mutation. GET list remains settlement history.

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

Platform admins and owners can manage settlements. Staff can preview their
own custody but cannot create settlements.

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
Platform admins and owners can view audit logs for the selected
club. Staff cannot access audit logs.

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
admins and owners can access all selected-club courts.

Summary returns compact operational booking counts, scoped court metadata, and
financial totals for the selected club and date/range. Platform admins and
owners see all selected-club courts. Managers see all selected-club courts under
the current club-level manager architecture. Staff can access summary for their
owners see all selected-club courts. Staff can access summary for their
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
summaries. Platform admins and owners can access them. Staff cannot.

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
- `owner_a`, `staff_a`
- `owner_b`, `staff_b`
- `owner_c`, `staff_c`

Main clubs:

- `demo-football-club`: happy-path testing; managers can settle and change
  pricing.
- `demo-restricted-club`: restricted manager settlement and pricing flags.
- `demo-football-club`: happy-path multi-court club.
- `demo-restricted-club`: single-court club.
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

### Development schema baseline

The Profile inheritance refactor resets the pre-release migration history.
Recreate the local development database before running `python manage.py migrate`;
existing databases from the discarded history cannot be upgraded in place.
First-party model apps each have one `0001_initial.py`. Recreate reused test
databases once with `pytest -n 4 --reuse-db --create-db`, then use the standard
`pytest -n 4 --reuse-db`.
