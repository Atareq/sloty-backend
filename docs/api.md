# API Reference

All endpoints are under `/api/v1/`. The OpenAPI schema is served at
`/api/v1/schema/` and Swagger UI at `/api/v1/docs/`.

## Authentication

- `POST /api/v1/auth/token/` obtains JWT access and refresh tokens using
  `phone_number` + `password`. Email is required account information, not a
  login identifier. There is no username.
- `POST /api/v1/auth/token/refresh/` refreshes an access token.
- `GET /api/v1/me/` returns the authenticated user's identity, `profile_role`,
  and account creator. Club scope is resolved from Profile extensions, not
  membership rows.
- `GET /api/v1/users/` lists and updates base user accounts for platform admin
  users. `POST /api/v1/users/` is not an onboarding endpoint.

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

## Business Actor IDs

Business actor identifiers in API fields and filters are `Profile.id` values.
They are separate from authentication `User.id`; a linked User may supply a
display name without changing the actor identity.

- Booking responses: `created_by` and `last_status_changed_by` are Profile IDs.
- Booking attempt responses and `attempted_by` filters: Profile IDs.
- Transaction responses: `created_by` (collector) and `cancelled_by` are
  Profile IDs. The transaction `created_by` and transaction-attempt
  `attempted_by` filters also take Profile IDs.
- Settlement responses expose `settled_by`, the Profile that performed the
  settlement; the `settled_by` list filter takes that Profile ID. Create,
  preview, and summary `collected_by` fields and the settlement list filter use
  the collector Profile stored as `Transaction.created_by`.
- Dashboard `collected_by`, report `staff`, and audit `actor` filters take
  Profile IDs. Report `staff` accepts an enabled StaffProfile in the selected
  club.
- Audit `actor`, dashboard and settlement `collected_by`, and report
  `staff_booking_activity[].staff` response IDs are Profile IDs.

Financial custody and settlement authority remain separate:
`Transaction.created_by` identifies the collector, while `Settlement.settled_by`
identifies the Profile who performed settlement.

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

## Clubs, Courts and Working Hours

- `/api/v1/clubs/`
- `/api/v1/clubs/{club_slug}/courts/`
- `GET /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`
- `PUT /api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`

Court working hours are court-scoped. The nested `working-hours` route returns
the selected court name, `pricing_configured`, and seven weekday rows. `PUT`
replaces the weekly schedule and pricing for that court:
replaces the weekly schedule and pricing for that court. Weekday numbering
follows the Sloty convention: `0` Saturday, `1` Sunday, `2` Monday,
`3` Tuesday, `4` Wednesday, `5` Thursday, `6` Friday.

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
Pricing periods define independent open/priced half-open `[start, end)` windows
with boundaries aligned to `slot_duration_minutes`. Adjacent periods are valid;
overlapping or duplicate periods are rejected. Gaps are closed time and generate
no slots. `pricing_periods=[]` represents a closed day; no synthetic gap row is
stored. Bookings inside or across a gap return `BOOKING_OUTSIDE_WORKING_HOURS`.

## Bookings

- `/api/v1/clubs/{club_slug}/bookings/`
- `GET /api/v1/clubs/{club_slug}/bookings/slots/`

Useful booking list filters:

- `court`
- `status`
- `source`
- `date`
- `date_from` (`YYYY-MM-DD` local midnight, or an ISO datetime; not clamped
  to now)
- `date_to` (`YYYY-MM-DD` inclusive local day, or an ISO datetime)
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
`date` or inclusive `date_from`/`date_to` query parameters. Each slot includes
`slot_price`, the current configured price for that specific slot. `FREE` and
`UNAVAILABLE` may appear as response-level slot states for UI display, but they
are not persisted booking statuses. On the current local day, elapsed hours
(`start_time` already in the past) are returned with `available=false`; past
calendar days stay FREE so historical bookings can still be created.
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
`DISMISSED`), `date`, `date_from`, and `date_to` (`YYYY-MM-DD` is inclusive
of that local day; ISO datetimes stay exact). Staff see only their own
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

## Transactions

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

## Booking Lifecycle

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

HOLD expiry is evaluated in real time by booking logic. A HOLD whose
effective deadline
`min(created + internal_hold_expiry_hours, start_time)` has passed does
not block overlap, create, reschedule, slot availability, calendar
occupancy, utilization, or court-usage occupancy, even while the row is
still `HOLD`. Reads never mutate status.

`python manage.py expire_hold_bookings` only persists `EXPIRED` on those
rows. It is idempotent. There is no Celery worker. Production cleanup is
the repo-owned systemd timer:

```bash
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-hold-expiry.service /etc/systemd/system/sloty-hold-expiry.service
sudo ln -sfn /srv/sloty/backend/scripts/systemd/sloty-hold-expiry.timer /etc/systemd/system/sloty-hold-expiry.timer
sudo systemctl daemon-reload
sudo systemctl enable --now sloty-hold-expiry.timer
```

The timer runs every 5 minutes (`Persistent=true`) as user `sloty` with
`prod.env`. The timer is not required for slot availability correctness.
Inspect cleanup with `journalctl -u sloty-hold-expiry.service`.

## Settlements

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
and `settled_by_name` (full name if present, otherwise phone number).
Settlement lines and preview transactions include booking customer name/phone
and booking start/end times.

Preview:

```text
GET /api/v1/clubs/{club_slug}/settlements/preview/?collected_by={profile_id}
GET /api/v1/clubs/{club_slug}/settlements/preview/?collected_by={profile_id}&court={court_id}
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

## Audit Log

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

## Dashboard and Availability

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

Useful query parameters:

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

## Reports

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

## Court Images

Court images use two immutable WebP objects stored in Cloudflare R2 and served
directly by the public image CDN. PostgreSQL stores only `display_key` and
`thumbnail_key`; Django derives their public URLs at response serialization and
never proxies image bytes. A source upload is processed in memory and is never
stored: JPEG, PNG, and WebP inputs are decoded, orientation-normalized,
metadata-stripped, transparency-flattened to white, then emitted as:

- **Display** — maximum 1920px long edge, WebP quality 84; use for galleries
  and detail views.
- **Thumbnail** — maximum 640px long edge, WebP quality 78; use for lists,
  cards, and previews.

Authenticated Court image endpoints follow normal Court scope:

```text
GET/POST    /api/v1/clubs/{club_slug}/courts/{court_id}/images/
DELETE      /api/v1/clubs/{club_slug}/courts/{court_id}/images/{court_image_id}/
PUT         /api/v1/clubs/{club_slug}/courts/{court_id}/images/reorder/
```

POST is multipart with one server-validated `image` file. Reorder accepts a
complete JSON `image_ids` list and rewrites positions to `0..N-1` atomically.
Admin/Owner manage images; Staff can read only the courts assigned to them.
Upload failures never persist a database key for a missing R2 object. Removing
a CourtImage detaches it first; its universal Image and both R2 objects are
removed only when no Court bridge remains. Cleanup failure keeps the orphan row
for a later retry.
