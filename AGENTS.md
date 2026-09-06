# AGENTS.md

## 1. Purpose

This file is the living guide for AI coding agents working in this repository.
Always read it before planning commands or code changes.

### Source of truth

Use documents in this order. A lower document must not override a higher one
when they conflict.

1. `AGENTS.md` — current engineering architecture, coding conventions, and
   repository workflow.
2. Locked contracts under `docs/` that describe implemented behavior, currently
   `docs/recurring-bookings-contract.txt` and
   `docs/financial-consistency-contract.txt`.
3. `README.md` — current setup and API/operator guidance.
4. Historical planning documents such as `docs/business-analysis.txt`,
   `docs/documentation.txt`, and `docs/sprints.txt` — product/planning context
   only. They may describe an earlier implementation state and must not be used
   as the current backend source of truth.

For booking-native recurrence or financial-consistency work, also read the
corresponding locked contract in `docs/`. Update this file whenever the
repository's backend structure, conventions, or workflow changes.

The goal is to keep this Django backend moving toward a disciplined
Django + Django REST Framework modular monolith without copying business logic
from any previous project.

Before implementing any new API, follow the API Implementation Checklist in
this file. This keeps future Codex prompts short and avoids repeating project
rules.

## 2. Project Summary

This is a new Django backend project named `sloty`.

Sloty is a sports court rental management system. The first market is local
clubs and courts in Assiut / Upper Egypt, starting with football courts. The
product starts as a free management tool for clubs and may later expand into a
marketplace or mobile booking app.

Current repo reality:

- Django project/config package: `config/`
- Current settings package: `config/settings/`
- Default local settings module: `config.settings.local`
- Current root URL config: `config/urls.py`
- Current local apps package: `apps/`
- Current implemented app: `apps/accounts/`
- Current implemented domain apps: `apps/clubs/`, `apps/courts/`,
  `apps/bookings/`, `apps/transactions/`, `apps/settlements/`,
  `apps/audit/`, `apps/dashboard/`, and `apps/reports/`
- Current shared app: `apps/common/`
- Current public API routes are versioned under `/api/v1/`
- Current API foundation endpoints include `/api/v1/schema/`,
  `/api/v1/docs/`, `/api/v1/auth/token/`, and
  `/api/v1/auth/token/refresh/`
- Current public utility endpoints include `/api/v1/egypt-locations/`
- Current deliberate public availability endpoint is
  `/api/v1/public/clubs/{club_slug}/courts/{court_id}/availability/`
- Current account endpoints include `/api/v1/me/` and platform-admin-only
  `/api/v1/users/`
- Current club users endpoint is `/api/v1/clubs/{club_slug}/users/`
- Optional SQL request performance logs are controlled by
  `SQL_QUERY_STATS_ENABLED`, `SQL_QUERY_STATS_VERBOSE`,
  `SQL_QUERY_STATS_WARN_QUERY_COUNT`, `SQL_QUERY_STATS_SLOW_QUERY_MS`,
  `SQL_QUERY_STATS_SLOW_REQUEST_MS`, `SQL_QUERY_STATS_MAX_QUERY_SAMPLES`,
  and `SQL_QUERY_STATS_MAX_SQL_LENGTH`. Monitoring is independent of
  `DEBUG` and is disabled by default.
- Project docs live under `docs/`
- Requirements are split into `requirements/base.txt` and `requirements/dev.txt`
- Style tooling exists through `.pre-commit-config.yaml`, `pyproject.toml`, and
  `setup.cfg`
- Sprint 1 implements backend foundation, JWT login/refresh URLs, the custom
  accounts user model, `/api/v1/me/`, and platform-admin user management APIs
- Sprint 2 implements club/court setup, club membership assignment, and setup
  API scoping
- Sprint 3 implements booking creation, booking list/detail and schedule
  filters, and application-level overlap protection
- Sprint 4 implements immutable booking transaction recording, booking payment
  summaries, and HOLD-to-CONFIRMED booking confirmation after the first valid
  transaction
- Sprint 5 implements manual booking lifecycle action endpoints, API v1
  routing, custom JWT convenience claims, and local demo seed data
- Sprint 6 implements club-scoped settlement/cash-closing for already-recorded
  transactions
- Sprint 7 implements club-scoped read-only audit logs for important business
  actions
- Sprint 8 implements read-only club operations dashboard, calendar, revenue,
  utilization, and court availability APIs
- Sprint 9 completes MVP booking lifecycle behavior with reason capture,
  rescheduling, completion that requires remaining amount already paid,
  automatic hold-expiry command support, and lifecycle audit logs
- Sprint 10 adds logged transaction cancelling for safe corrections, excludes
  cancelled transactions from financial totals and settlements, and recalculates
  booking payment status from valid transactions
- Court usage reporting adds a read-only `apps/reports/` analytics app and
  `GET /api/v1/clubs/{club_slug}/reports/court-usage/`
- Planned shared app name is `apps/common/`
- Domain apps beyond `accounts`, `clubs`, `courts`, `bookings`,
  `transactions`, `settlements`, `audit`, `dashboard`, and `reports` are not
  implemented yet

Planned project direction:

- Treat the backend as a Django + DRF modular monolith.
- Add each business area as a focused Django app under `apps/<domain>/`.
- Keep business workflows in service modules and keep HTTP views thin.
- Add shared infrastructure only when repeated patterns justify it.
- Planned app layout: `accounts`, `clubs`, `courts`, `bookings`,
  `transactions`, `settlements`, `pricing`, `audit`, and `common`.
- Only `accounts`, `clubs`, `courts`, and `bookings` should exist through
  Sprint 3. Sprint 4 adds `transactions`.
- Recurring reservations are Booking-native. Do not add a recurring app,
  recurring series/agreement model, or dedicated deposit ledger.

## 3. Architecture Overview

Use a modular-monolith architecture:

- Each domain owns its models, serializers, views, URLs, services, validators,
  filters, tasks, permissions, constants, and tests.
- Cross-domain behavior should go through explicit services instead of hidden
  imports or signals.
- Shared helpers belong in a shared module only after they are clearly reusable.
- Avoid adding features or business rules unless the current task explicitly
  asks for them.

Current implemented app:

- `apps/accounts/` contains the custom user model, identity fields, phone
  number, creator tracking, platform authority flag, account admin
  registration, account serializers/views, and account permission helpers.
- `User` is identity plus platform authority only. It has
  `is_platform_admin`; it does not store club-scoped OWNER, MANAGER, or STAFF
  roles.
- Platform user management APIs are restricted to Platform Super Admin users.
  They may manage identity fields, `is_active`, and `is_platform_admin`; generic
  create may create platform admin users only. They must not expose or accept
  Django `is_staff`, `is_superuser`, or passwords in read responses.
- Do not create active non-platform users from generic user APIs. Club business
  users must be created through club-scoped membership onboarding so `User` and
  active `ClubMembership` are created atomically.
- Do not create orphan business users in tests, seed data, or APIs unless a
  test explicitly checks rejection or diagnostics.
- It must not contain club, court, booking, transaction, settlement, pricing,
  staff shift, marketplace, or assignment business logic.
- `apps/clubs/` contains club setup, club membership assignment logic, and the
  central `ClubAccessContext` club-scoped access layer.
- `Club` has a unique `slug` used by club-scoped business API URLs.
- `Club.governorate` and `Club.city` are controlled Egypt location code
  choices from `apps/common/egypt_locations.py`; `Club.city` must belong to
  `Club.governorate`.
- `Club.address` remains detailed free text. `Club.area` has been removed;
  do not add or accept `area` in future Club APIs unless a new task explicitly
  reintroduces it.
- `ClubMembership` is the single source of OWNER, MANAGER, and STAFF authority
  inside a club. STAFF memberships are tied to a court through
  `ClubMembership.court`.
- `ClubMembership` stores manager-specific
  `manager_can_settle_transactions` and `manager_can_change_pricing` flags.
  They are meaningful only for MANAGER memberships and must remain false for
  OWNER and STAFF memberships. `manager_can_change_pricing` gates manager
  updates to court working hours and pricing periods.
  `manager_can_settle_transactions` gates manager settlement access.
- `POST /api/v1/clubs/{club_slug}/memberships/` supports club-scoped onboarding:
  nested user data plus membership role/court are persisted together through
  `apps/clubs/services.py`.
- `PATCH` `is_active` deactivates or reactivates a current membership. This is
  temporary: the row remains in current membership lists and can be turned
  back on. Deactivating an operational STAFF membership is blocked with
  `MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED` when that user's canonical Current
  Custody in the selected club is non-zero.
- `DELETE /api/v1/clubs/{club_slug}/memberships/{id}/` soft-deletes a
  membership (`deleted_at` / `deleted_by`, `is_active=false`). Soft-deleted
  rows are excluded from current membership and club-user lists, grant no
  club access, and cannot be reactivated with PATCH. The `User` account and
  historical bookings/transactions/settlements/audit rows are preserved.
  Audit action is `MEMBERSHIP_DELETED`. Owners cannot delete OWNER
  memberships. Deleting an operational STAFF membership is blocked with
  `MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED` while canonical Current Custody is
  non-zero. Recreating the same club + user + role + court identity after
  soft delete is rejected with `MEMBERSHIP_DELETED_CANNOT_RECREATE`. Whether
  a deleted user may later be added with a different role or court is
  unresolved. Implementation uses custom `deleted_at` / `deleted_by` fields,
  not `SafeDeleteModel`; keep django-safedelete installed.
- `GET /api/v1/clubs/{club_slug}/users/` is a read-only, club-scoped,
  membership-based users list. Platform admins and owners see all selected-club
  current (non-deleted) memberships, including deactivated rows. Managers can
  list active MANAGER/STAFF employees read-only.
  Staff cannot list club users.
- `apps/courts/` contains court setup and court working hours logic.
- `Court` keeps `default_price` only as legacy migration data. It stores
  `slot_duration_minutes`,
  `requires_digital_payment_reference`, and `internal_hold_expiry_hours`.
  Sprint 4 transactions use `requires_digital_payment_reference` for manual
  payment-reference validation. Automatic hold expiry is implemented through
  `python manage.py expire_hold_bookings` and `expire_due_hold_bookings()`.
  The command filters likely-due HOLD rows in the database using
  `min(created + internal_hold_expiry_hours, start_time)`, then re-checks
  expiry under `select_for_update()`. Do not add a Celery scheduler. Production
  and staging must schedule `python manage.py expire_hold_bookings` (cron or
  equivalent, typically every 5 minutes). The UI may promise automatic HOLD
  cancel only when that job is actually running.
- Working-hour pricing uses child pricing periods tied explicitly to
  `CourtWorkingHour`, not one global morning/night price on `Court` and not one
  price field on `CourtWorkingHour`. `Court.default_price` must not be used for
  new booking creation, rescheduling, or slot availability prices.
- Court working hours are court-scoped under
  `/api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`. They remain one
  `CourtWorkingHour` row per court plus weekday; child pricing periods define
  operating hours. A weekday with no pricing periods is closed. Do not add
  replacement open/close/closed fields on `CourtWorkingHour`, move
  working-hour fields onto `Court`, or convert them to one-to-one settings.
- Club/court scope must come from active `ClubMembership` rows, not from direct
  club or court fields on `User`.
- `apps/bookings/` contains booking creation, list/detail APIs, schedule-style
  filters, price snapshot calculation, and active booking overlap protection.
- Current booking source values are `MANUAL`, `ADMIN_CORRECTION`, and
  `RECURRING`. Clients must not post `source=RECURRING`; they start a
  recurrence with write-only `is_recurring=true`. Only a Platform Super Admin
  may create an `ADMIN_CORRECTION` booking.
- Sprint 3 booking PATCH only changes `customer_name`, `customer_phone`, and
  `notes` on non-locked bookings. It must not change court, start/end time,
  status, source, or price.
- Booking list filters currently supported by the API are `court`, `status`,
  `source`, `date`, `date_from`, `date_to`, `needs_action`, `overdue`,
  `has_remaining_amount`, deprecated `remaining_amount_gt`, `ended`,
  `hold_expiring`, `search`, and `upcoming`.
- `search` matches `customer_name` (case-insensitive contains),
  `customer_phone` including practical Egyptian phone variants such as
  `01012345678`, spaced digits, and `+201012345678`, and `notes`. It runs on
  the already authorized/scoped queryset and composes with pagination and
  other filters. Do not add a separate `notes_search` parameter.
- Booking list responses expose `notes` so operational cards do not require a
  follow-up detail request.
- `upcoming=true` means `status` in `HOLD`/`CONFIRMED` and `end_time > now`,
  so an in-progress booking remains upcoming. Terminal statuses are excluded.
- Booking list/detail expose read-only `hold_expires_at`: for `HOLD` this is
  `min(Booking.created + Court.internal_hold_expiry_hours, Booking.start_time)`
  (the same rule used by `expire_hold_bookings`); for every other status it is
  `null`.
- Booking create accepts optional `client_request_id` as a UUID idempotency key.
  Normal online bookings may omit it. When present, uniqueness is scoped to the
  selected Club and retained on the Booking row for the life of that row. The
  same `client_request_id` plus the same logical request returns the original
  Booking with HTTP 200 and does not create another Booking or Audit event.
  Reusing the same `client_request_id` for a different logical request returns
  HTTP 409 with `BOOKING_CLIENT_REQUEST_MISMATCH`. Concurrent requests with the
  same Club/key are serialized by row locks and the database uniqueness
  constraint.
- New booking creation and rescheduling must be inside configured court working
  hours and fully covered by pricing periods. No `outside_working_hours` flag is
  stored.
- `GET /api/v1/clubs/{club_slug}/bookings/slots/` returns generated schedule
  slots for a selected court and date/date range, including current configured
  `slot_price`. `FREE` and `UNAVAILABLE` are response-level availability states
  only; do not add them to `Booking.Status`.
- Slot availability and overlap checks treat `HOLD`, `CONFIRMED`, `COMPLETED`,
  and `NO_SHOW` bookings as blocking. `CANCELLED` and `EXPIRED` bookings release
  their slots.
- The standard unavailable-slot business error code is
  `BOOKING_SLOT_UNAVAILABLE`. Starting a new recurring booking whose selected
  base slot is free but whose weekly pattern conflicts later returns
  `RECURRING_UNAVAILABLE` with recurrence conflict details.
- Sprint 9 booking lifecycle actions are manual endpoints on the existing
  `BookingViewSet`: cancel, complete, no-show, reschedule, and expire.
  Automatic hold expiry is exposed through the `expire_hold_bookings`
  management command only; no Celery scheduler or background worker exists.
- `apps/transactions/` contains immutable booking transaction recording,
  transaction create/list/detail/cancel APIs, payment reference uniqueness inside
  a club, and booking payment summary support.
- Transaction creation confirms a HOLD booking to CONFIRMED. Other booking
  lifecycle actions are handled by the Sprint 5 booking lifecycle service.
- Transaction corrections use a logged cancel of the original row followed by
  the normal create endpoint. Refunds, reversals, online payment gateway logic,
  and platform commission calculation remain future work.
- Business APIs for memberships, courts, working hours, bookings, and
  transactions are club-scoped under `/api/v1/clubs/{club_slug}/...`.
- Club user listing is club-scoped under
  `/api/v1/clubs/{club_slug}/users/`, is read-only, and returns users through
  `ClubMembership` rows. Platform admins and owners see all selected-club
  memberships. Managers can list active MANAGER/STAFF employees read-only.
  Staff cannot list club users. Do not add `User.role`, `User.club`, or
  `User.court`.
- Login remains global. The frontend logs in, calls `/api/v1/me/` to read active
  memberships and club slugs, then sends selected-club requests to
  `/api/v1/clubs/{club_slug}/...`. Never trust frontend-selected club context
  without backend verification.
- Token obtain may accept optional `club_slug` and returns custom JWT claims
  for frontend convenience: `user_id`, `role`, `name`, and optional `club_id`
  and `court_id`. These claims are derived from `User` and active
  `ClubMembership` rows at token issue time; `User` still does not store
  club-scoped roles or club/court fields. Business endpoints must still verify
  active database-backed club access through `ClubAccessContext` and must not
  trust JWT club/court claims alone.
- Use “club-scoped access”, “club access”, or “club context” terminology only.

Planned app pattern:

```text
apps/<domain>/
    models.py
    serializers.py
    views.py
    urls.py
    services.py
    validators.py
    filters.py
    constants.py
    tasks.py
    permissions.py
    tests/
```

If the app is still small, it is acceptable to start with a smaller subset of
these files. Mark missing files as planned patterns in documentation or PR notes
instead of pretending they already exist.

`permissions.py` remains an optional owning-app file when a domain truly needs
DRF permission classes. For `apps/transactions/` work, do not create
`apps/transactions/permissions.py` by default; transaction access is expected
to go through `ClubAccessContext`.

## 4. Preferred Request Flow

Preferred request flow:

```text
urls -> ViewSet/APIView -> serializer -> validator/service -> model/ORM -> response serializer
```

Rules for the flow:

- URL routing should only route requests.
- Views should coordinate HTTP concerns, permissions, queryset scoping, and
  serializer selection.
- Serializers should validate basic request shape and define response
  representation.
- Validators should hold reusable business validation and domain rules.
- Services should own workflows, state transitions, multi-model writes, and
  transactions.
- Models should define persistence, relationships, constraints, indexes, and
  lightweight invariants.

## 5. Folder Responsibilities

`config/`

- Django project/config package.
- Contains `urls.py`, `asgi.py`, `wsgi.py`, and the `settings/` package.
- `config/settings/base.py` contains shared settings and environment-driven
  defaults.
- `config/settings/local.py`, `config/settings/test.py`, and
  `config/settings/production.py` currently import from `base.py` and should
  only diverge when the environment needs a documented difference.

`docs/`

- Product and planning documentation.
- Locked implemented contracts such as `docs/recurring-bookings-contract.txt`
  are current business/API authority after `AGENTS.md`.
- `docs/business-analysis.txt`, `docs/documentation.txt`, and
  `docs/sprints.txt` are historical planning documents. Read them for product
  context only. Do not implement or restore architecture from them when they
  conflict with this file or current locked contracts.

`locale/`

- Project-level Django translation catalogs. Arabic API translations currently
  live under `locale/ar/LC_MESSAGES/`; compile `.po` changes to `.mo` files
  before relying on translated runtime responses.

`apps/`

- Container for local Django apps.
- New domain apps should be created as `apps/<domain>/`.
- Keep domain code inside the owning app unless it is genuinely reusable.

`apps/accounts/`

- Current account app.
- Contains the custom `User` model, `phone_number`, nullable `created_by`,
  `is_platform_admin`, account admin registration, account serializers/views,
  `/api/v1/me/`, platform-admin-only `/api/v1/users/`, and account permission
  helpers.
- `User.is_platform_super_admin()` is a temporary compatibility helper that
  returns `is_platform_admin`.
- `/api/v1/me/` exposes `account_created_by` as a stable nested object from
  `User.created_by`, or `null` when the creator is unknown. This is the account
  creator, not the membership creator.
- Auth failures expose stable top-level codes through the shared exception
  handler: expired JWT access tokens return `SESSION_EXPIRED`; stale tokens for
  inactive users return `USER_INACTIVE`; stale tokens for deleted users return
  `USER_DELETED`.
- Club-scoped authenticated endpoints use `CLUB_ACCESS_REVOKED` with
  `details.club_slug` when the user has a valid account but no longer has
  active access to the selected Club. Token obtain uses the same code when an
  optional valid `club_slug` is supplied but the user has no active membership
  there. This lets clients purge only the affected Club scope instead of all
  user data.
- `MeAPIView` must load `created_by` with `select_related()` and active
  memberships with a `Prefetch(..., to_attr=...)` that selects `club` and
  `court`; `UserMeSerializer` should consume the prefetched attribute instead
  of issuing per-membership queries.
- Do not add or reintroduce club-scoped business roles on `User`.
- `/api/v1/users/` must not create active non-platform users; use the
  club-scoped membership endpoint for club owners, managers, and staff.
- `apps/accounts/services.py` owns lightweight account diagnostics such as
  `find_orphan_business_users()`.
- Do not place unrelated domain behavior here.

`apps/clubs/`

- Club setup app.
- Contains `Club` and `ClubMembership`.
- `Club.slug` is the stable club-scoped API identifier.
- `ClubMembership` assigns active OWNER, MANAGER, and STAFF authority inside a
  club.
- OWNER and MANAGER memberships are club-level and must not have a court.
- STAFF memberships are court-scoped and must point to a court in the same
  club.
- Active MANAGER memberships are currently limited to one club per user.
  Active STAFF memberships are currently limited to one court assignment per
  user.
- Club-scoped member onboarding creates a non-platform active `User` plus active
  `ClubMembership` in one `transaction.atomic()` workflow.
- Club membership has three operational states: active (`is_active=true`,
  `deleted_at` null), deactivated (`is_active=false`, `deleted_at` null,
  reactivatable), and soft-deleted (`deleted_at` set, not reactivatable).
  Access-granting queries use active non-deleted memberships only.
  Operational STAFF deactivation and soft delete are blocked when canonical
  Current Custody for that user in the selected club is non-zero; settle current
  money first.
  Recreating the same club + user + role + court after soft delete is rejected
  with `MEMBERSHIP_DELETED_CANNOT_RECREATE`. Soft delete uses custom
  `deleted_at` / `deleted_by` fields rather than `SafeDeleteModel`.
- `apps/clubs/access.py` contains `ClubAccessContext`, the central source of
  truth for club-scoped access checks and scoped querysets.
- `apps/clubs/mixins.py` contains `ClubScopedAccessMixin` for club-scoped
  ViewSets.
- `apps/clubs/services.py` owns membership onboarding workflows; serializers and
  views should not create club users and memberships as separate requests.
- Do not place court, booking, transaction, settlement, pricing, or audit
  behavior here.

`apps/courts/`

- Court setup app.
- Contains `Court`, `CourtWorkingHour`, and `CourtWorkingHourPricePeriod`.
- Platform admins and club owners can create and update courts. Managers can
  list/retrieve courts but cannot update court fields. Court PATCH/PUT object
  permission uses `ClubAccessContext.can_update_court()`; managers and staff
  receive 403. `manager_can_change_pricing` does not grant Court model field
  updates. Staff can list/retrieve only their assigned court and cannot update
  courts.
- Platform admins, owners, and managers with membership-level
  `manager_can_change_pricing=True` can manage working hours and pricing for
  accessible courts. Managers without that flag cannot update weekly working
  hours because boundary changes can invalidate pricing.
- The nested court working-hours endpoint is the primary API for frontend
  settings pages. The older `/court-working-hours/` route is read-compatible;
  POST/PATCH writes are rejected with `WORKING_HOURS_USE_WEEKLY_ENDPOINT`.
- `Court.minimum_deposit` controls the first normal booking payment threshold
  and late-cancellation retained amount. `Court.cancellation_refund_notice_days`
  is the shared refund-notice policy for normal booking cancellation and
  Booking-native recurring cancellation.
- `CourtStaffAssignment` has been removed. Staff access is represented by
  `ClubMembership(role=STAFF, court=<court>)`.
- Do not place booking, transaction, settlement, pricing, or audit behavior
  here.

`apps/bookings/`

- Booking foundation app.
- Contains `Booking`, booking serializers, scoped booking viewsets, and booking
  creation/lifecycle services.
- New bookings start as `HOLD`; in Sprint 4 a valid booking transaction
  confirms a HOLD booking to CONFIRMED.
- `total_price` is calculated by the backend from the selected court's
  working-hour pricing periods and stored as a historical agreed-price
  snapshot. Clients must not control booking price.
- Overlap protection is currently application-level: `HOLD`, `CONFIRMED`,
  `COMPLETED`, and `NO_SHOW` bookings block overlapping bookings on the same
  court.
- Booking access is club-scoped through `ClubAccessContext`. Staff users can
  list and create bookings only for their assigned court.
- Creating bookings on inactive clubs or inactive courts is rejected.
- `COMPLETED`, `CANCELLED`, `NO_SHOW`, and `EXPIRED` bookings are treated as
  locked for Sprint 3 update behavior. Only `CANCELLED` and `EXPIRED` release
  their time slot for a new booking.
- Sprint 5 lifecycle transitions are service-layer controlled through
  action-specific service functions in `apps/bookings/services.py`: cancel,
  complete, no-show, reschedule, expire, and due-hold expiry. The services use
  `transaction.atomic()`, `select_for_update()`, explicit status validation,
  and `ClubAccessContext` access checks.
- Allowed lifecycle transitions are `HOLD -> CANCELLED`,
  `HOLD -> EXPIRED`, `CONFIRMED -> CANCELLED`,
  `CONFIRMED -> COMPLETED`, and `CONFIRMED -> NO_SHOW`.
- Terminal statuses `COMPLETED`, `CANCELLED`, `NO_SHOW`, and `EXPIRED` cannot
  transition further.
- `cancel` accepts an optional reason for platform admins, owners, and managers;
  staff must provide a non-empty cancellation reason. Normal bookings cannot be
  cancelled after `start_time`; cancellation uses the current
  `Court.cancellation_refund_notice_days` and `Court.minimum_deposit` to
  calculate retained/refund amounts. Refunds are internal negative booking
  `Transaction` rows with `transaction_type=REFUND`.
- `no-show` is allowed only from `CONFIRMED` and stores an optional reason.
- `reschedule` is allowed only from `HOLD` or `CONFIRMED`; the new court must
  belong to the selected club, pass `ClubAccessContext` access checks, match the
  court slot duration, and avoid overlaps with blocking bookings while excluding
  the current booking.
- Rescheduling keeps existing transactions attached to the same booking. If the
  recalculated price is higher, update `total_price`; if it is lower or equal,
  keep the existing `total_price`. Cancellation refund still uses the current
  `start_time` after reschedule. Restoring previously lost refund rights is a
  known loophole; do not add refund-floor / original-start fields, do not use
  AuditLog as financial state, and do not accept previous refund amounts from
  the client until `APPROVAL REQUIRED — RESCHEDULE REFUND PERSISTENCE`.
- `complete` is allowed only from `CONFIRMED`. If a dynamic remaining amount
  exists, completion is rejected with
  `BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT` and 409 Conflict. The backend must
  not auto-create a remaining CASH transaction during completion.
  `confirm_collect_remaining_cash` is a deprecated no-op request field kept for
  compatibility; it does not collect cash.
- Manual `expire` is allowed only from `HOLD`. Automatic due-hold expiry uses
  `python manage.py expire_hold_bookings`, sets `EXPIRED`, records
  `expired_at`, and audits with actor `None`. The command is not a background
  worker; operations must schedule it. `hold_expires_at` on list/detail uses
  the same `min(created + internal_hold_expiry_hours, start_time)` rule.
- Booking lifecycle traceability fields are `cancellation_reason`,
  `no_show_reason`, `reschedule_reason`, `completed_at`, `cancelled_at`,
  `no_show_at`, and `expired_at`. Do not add payment status or cached remaining
  amount fields.
- Do not place transaction creation logic, settlement, rescheduling,
  dashboard, marketplace, or notification behavior in `bookings`, except the
  hold-expiry command explicitly owned by booking lifecycle services.

## Booking Slot Availability and Completion Rules

### Slot availability response

Slot availability endpoints may return `FREE` as a response-level availability
state.

`FREE` must not be added to `Booking.Status`.

A generated slot is `FREE` when no blocking booking overlaps it.

Blocking booking statuses include:

```text
HOLD
CONFIRMED
COMPLETED
NO_SHOW
```

Non-blocking statuses include:

```text
CANCELLED
EXPIRED
```

Completed bookings must continue to block their slots.

### No double booking

The backend must prevent creating or rescheduling a booking into a slot that
overlaps a blocking booking.

Do not rely only on the frontend to disable occupied slots.

The standard error code for occupied/unavailable slots is:

```text
BOOKING_SLOT_UNAVAILABLE
```

### Completion requires full payment

A booking cannot be marked as `COMPLETED` until its remaining amount is fully
paid.

If `remaining_amount > 0`, raise:

```text
BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT
```

This is a business-state conflict and should return 409 Conflict.

### FREE is display state only

`FREE` is used to help the frontend render the schedule clearly.

It is not a persisted booking status and must not be stored in the database.

## Working-Hour Pricing

Court booking prices are configured through pricing periods attached to
`CourtWorkingHour`.

`Court.default_price` is legacy migration data and must not be used for new
booking calculations.

Every configured open weekday must have complete, contiguous, non-overlapping
pricing periods. A weekday with no pricing periods is closed and is not a
pricing configuration error.

Pricing-period boundaries and booking times must align with
`Court.slot_duration_minutes`.

`Booking.total_price` is a historical agreed-price snapshot and must not be
recalculated when court pricing changes.

Booking slot responses expose the current configured `slot_price`. A generated
slot with missing/corrupt pricing uses response-only `UNAVAILABLE` with
`slot_price=null`; do not add `UNAVAILABLE` to `Booking.Status`.

Court Usage Report financial values use `Booking.total_price`, not current
pricing periods.

`apps/transactions/`

- Transaction recording and Sprint 10 correction app.
- Contains `Transaction`, transaction serializers, transaction viewsets,
  transaction creation/cancel services, and transaction admin registration.
- Do not create `apps/transactions/permissions.py` by default.
- Transaction access must be centralized through
  `apps/clubs/access.py -> ClubAccessContext`.
- `TransactionViewSet` must use `ClubScopedAccessMixin`.
- Transaction serializers must receive `context["club_access"]`.
- Transaction views should call `access.scoped_transactions_queryset()` for
  list/detail scoping. Staff (not owner/manager/platform admin) see only
  transactions they collected (`created_by` is the current user) on their
  assigned court. `?created_by=` cannot broaden that Staff scope. Owner,
  manager, and platform admin retain club/court-wide transaction lists.
- Transaction creation should call
  `access.can_create_transaction_for_booking(booking)`.
- Object-level transaction checks, when needed, should call
  `access.can_access_transaction(transaction)`.
- Transaction cancel authority should call
  `access.can_cancel_transaction(transaction)`. Platform admins may cancel any
  eligible scoped transaction; owners, managers, and staff may cancel only their
  own eligible scoped transactions.
- Do not duplicate `ClubMembership` queries inside transaction views,
  serializers, or services.
- Do not create independent per-app transaction permission logic. If a
  transaction-specific DRF permission class seems absolutely necessary, stop
  and explain why before adding it.
- Transactions are immutable financial history through the API: no PATCH, PUT,
  DELETE, or reversal behavior exists. Normal API creates are
  `transaction_type=PAYMENT` with positive amounts. Booking cancellation
  services may internally create `transaction_type=REFUND` rows with negative
  amounts. Corrections use
  `POST .../transactions/{id}/cancel/` with a required reason, followed by normal
  transaction creation.
- The first active PAYMENT for a normal booking must be at least
  `min(booking.court.minimum_deposit, booking.total_price)`. Only active PAYMENT
  rows count toward paid/remaining booking amounts. Active REFUND rows count as
  signed negative settlement/revenue rows and cannot be cancelled through the
  normal transaction correction flow.
- Cancelled transactions remain visible and may be filtered with `is_cancelled`, but
  only non-cancelled transactions count toward settlements, calendar summaries,
  and dashboard revenue.
- Transaction list filters currently supported by the API are `booking`,
  `court`, `payment_method`, `transaction_type`, `date`, `date_from`,
  `date_to`, `created_by`, `is_cancelled`, `settlement_status`, `settlement`,
  `search`, and `ordering`.
- `search` matches `booking.customer_name` (case-insensitive contains),
  `booking.customer_phone` using the shared Egyptian phone-variant helper
  also used by booking search, and `payment_reference` (case-insensitive
  contains, so `8821` matches `IPN-882192`).
- `settlement` filters `settlement_line__settlement_id` on the already
  scoped transaction queryset. It cannot leak another club or expand Staff
  self-only collections.
- `ordering=-created` (default) is newest first with `-created, -id`.
  `ordering=created` is oldest first with `created, id`. Arbitrary field
  ordering is not allowed.
- Transaction list/detail include `booking_customer_name`,
  `booking_customer_phone`, `booking_start_time`, and `booking_end_time` from
  the related booking. The queryset already `select_related("booking")`.
- Transaction list date filters use `Transaction.created` as the date authority.
  Date-only `date_from`/`date_to` values mean complete local calendar days with
  an exclusive next-day upper bound.
- Already cancelled or settled transactions and transactions attached to terminal
  bookings cannot be cancelled. If no valid payment remains on a confirmed
  booking, the cancel service returns the booking to `HOLD` in the same atomic,
  row-locked workflow.
- Transaction creation may change booking status only from HOLD to CONFIRMED.
  Transaction cancel may change booking status from CONFIRMED to HOLD when no
  valid payment remains, including recurring bookings.

`apps/settlements/`

- Sprint 6 settlement/cash-closing app.
- Contains `Settlement`, `SettlementTransaction`, settlement serializers,
  settlement viewsets, settlement filters, settlement services, and settlement
  admin registration.
- Settlement APIs are club-scoped under
  `/api/v1/clubs/{club_slug}/settlements/`.
- Settlement access is centralized through
  `apps/clubs/access.py -> ClubAccessContext`.
- `SettlementViewSet` must use `ClubScopedAccessMixin`.
- Settlement serializers must receive `context["club_access"]`.
- Settlement views should call `access.scoped_settlements_queryset()` for
  list/detail scoping.
- Settlement preview is read-only behavior available through
  `GET .../settlements/preview/`. Any active selected-club user can preview
  their own unsettled transactions, and omitted `collected_by` defaults to
  `request.user`. Preview must not create `Settlement`,
  `SettlementTransaction`, audit rows, locks, or transaction state changes.
- `GET .../settlements/unsettled-summary/` is the management all-collector
  read model of current unsettled custody. Candidate semantics match preview:
  selected club, `is_cancelled=false`, no settlement line, collector=
  `Transaction.created_by`, existing court/access rules. `period_start` is
  the earliest unsettled transaction for that collector; `period_end` is
  request time. Platform admins and owners see eligible selected-club
  collectors. Managers need `manager_can_settle_transactions=True`, cannot
  see OWNER money, and have `can_approve=false` for their own row. Staff and
  managers without the flag cannot use this endpoint. Optional `collected_by`
  and `court` follow preview access validation. This is grouped ORM
  aggregation, not one preview per employee. POST create and GET list remain
  the mutation and history APIs; do not add a second receive/history route.
- `apps.settlements.services.get_current_unsettled_transactions()` is the one
  authoritative Current Custody candidate rule used by summary, preview,
  create, and dashboard current-money fields. It contains no date,
  payment-method, or settlement-status parameters. Current Custody includes all
  non-cancelled signed Transactions with no settlement line, regardless of
  age. Optional `court` filtering applies only when explicitly selected and
  authorized; the default is all Courts accessible to the actor.
- Current Custody summaries expose candidate count, signed net, payment total,
  refund total, signed payment-method breakdown, earliest candidate time, and
  request time. Zero-net and negative-net collectors remain visible when they
  have candidates. A zero candidate set is a distinct state.
- Settlement list/detail include `court_name` (`Court.name` or `null` when
  `court` is null) and `settled_by_name` using the same display-name rule as
  `collected_by_name` (full name if present, otherwise username). Settlement
  lines, preview transactions, and transaction list/detail include
  `booking_customer_name`, `booking_customer_phone`, `booking_start_time`,
  and `booking_end_time`. Preview `court_name` still uses `""` when court is
  omitted. Do not add `created_by_name` unless a current UI contract requires
  it.
- Platform admins and owners can preview active users in the selected club and
  can create, list, retrieve, and mark settlements as settled.
- Managers can create/list/retrieve/mark settlements only when their own
  membership has `manager_can_settle_transactions=True`. Managers with that
  flag can preview/create for active STAFF and MANAGER users in the selected
  club, but not OWNER users by default. Managers can preview their own
  transactions as a dry run but must not approve/create their own settlement.
- Managers without settlement permission and Staff can preview their own
  unsettled transactions and list/retrieve only their own settlements. They
  cannot choose another employee, create/approve settlements, mark settlements
  settled, or manage another user's financial data.
- For settlement create/approval, platform admins and owners may approve their
  own collected transactions. Managers and staff must not approve their own
  settlement.
- Settlement preview/approval is user/collector-based through
  `Settlement.collected_by`. `POST .../settlements/` is the approval path and
  requires `collected_by`; optional `court` limits the candidates to that court.
  Date ranges are not accepted. The backend selects all valid, non-cancelled,
  unsettled transactions where `Transaction.created_by` is the selected
  collector in the selected club and accessible court scope, computes
  `period_start` from the earliest selected transaction, and sets `period_end`
  to creation time.
- Settlement Create re-queries and locks the same authoritative candidate rule.
  Never accept Preview rows or client-provided Transaction IDs as authoritative.
- New API-approved settlements are created directly as `SETTLED` with
  `created_by`, `settled_by`, and `settled_at` set to the approving actor. The
  `PENDING` status and mark-settled endpoint remain only for legacy pending
  settlements and manual testing data; new API approval must not create
  `PENDING` settlements.
- `SettlementTransaction.transaction` is a `OneToOneField` to `Transaction`;
  this prevents one transaction from being included in more than one
  settlement while keeping transaction rows immutable.
- Settlements include non-cancelled already-recorded signed booking
  transactions by club, collected_by user, and unsettled state. Booking
  lifecycle status is not used to decide settlement inclusion in Sprint 6.
- Settlements include signed refund Transactions created by the Booking
  cancellation lifecycle. They do not implement gateway reversals, commission,
  payout automation, or automatic settlement jobs.
- Settlement filters live in `apps/settlements/filters.py` and must follow the
  standard FilterSet pattern.
- `seed_demo_data` maintains multi-club settlement examples: unsettled
  transactions, pending settlements, and settled settlements for manual
  settlement testing.
- Do not create `apps/settlements/permissions.py` by default. If a settlement
  permission class is necessary, keep it as a thin centralized wrapper in
  `apps/clubs/permissions.py`.

`apps/audit/`

- Sprint 7 audit/activity trail app.
- Contains `AuditLog`, audit serializers, audit viewsets, audit filters, audit
  services, and audit admin registration.
- Audit APIs are club-scoped under
  `/api/v1/clubs/{club_slug}/audit-logs/`.
- Audit access is centralized through
  `apps/clubs/access.py -> ClubAccessContext`.
- `AuditLogViewSet` must use `ClubScopedAccessMixin`.
- Platform admins, owners, and managers can list/retrieve audit logs in the
  selected club. Staff cannot access audit logs in Sprint 7.
- Audit logs are append-only and read-only through the API. Do not expose API
  create, update, or delete actions for audit logs.
- Audit logs must be created explicitly from service-layer business actions,
  not Django signals.
- Audit action machine values such as `BOOKING_CREATED` and
  `TRANSACTION_CANCELLED` are stable database/API values and must not be
  translated or renamed. API serializers expose localized `action_label` from
  `get_action_display()` for UI display while keeping `action` for filtering
  and frontend logic.
- Audit list/detail expose human-readable `actor_name`, `court_name`, their
  source labels, and an entity-specific `summary`. Booking summaries contain
  customer/Court/time/status context; Transaction summaries contain
  customer/signed amount/method/collector/Court context; Settlement summaries
  contain collector/amount/count/approver/time context.
- New audited Booking, Transaction, and Settlement events use small snapshot
  helpers to persist event-time display facts in existing JSON. Old rows are not
  backfilled. Existing JSON is preferred; already-selected current actor/Court
  relations are labeled `CURRENT_RELATION_FALLBACK`, and serializers must never
  query the underlying business entity per audit row.
- Audited Sprint 7 actions are booking create/update/lifecycle transitions,
  transaction creation, settlement creation, and mark-settled.
- Sprint 9 adds `BOOKING_RESCHEDULED` and requires audit logs for cancellation,
  no-show, reschedule, completion, manual expiry, automatic expiry, and auto
  cash transaction creation on completion.
- Sprint 10 adds `TRANSACTION_CANCELLED`. A cancel records before/after cancel and
  booking status data plus reason metadata; a CONFIRMED-to-HOLD recalculation
  also records an explicit `BOOKING_UPDATED` audit log.
- Membership soft delete records `MEMBERSHIP_DELETED`.
- Audit filters live in `apps/audit/filters.py` and must follow the standard
  FilterSet pattern.
- Audit logging does not implement reports, dashboards, exports, correction
  workflows, or broad field-history tracking.
- Do not create `apps/audit/permissions.py` by default. If an audit permission
  class is necessary, keep it as a thin centralized wrapper in
  `apps/clubs/permissions.py`.

`apps/dashboard/`

- Sprint 8 read-only operations dashboard app.
- Contains dashboard serializers, services, views, and URL routing. It has no
  models or migrations in Sprint 8.
- Dashboard APIs are frontend summary APIs for availability, calendar,
  operational/financial summary, overview, revenue, and court utilization.
- Availability and calendar are operational APIs. Staff can access only their
  assigned court through `ClubAccessContext`.
- `/api/v1/public/clubs/{club_slug}/courts/{court_id}/availability/` is the
  deliberate sanitized public availability contract. It uses the same slot
  calculation as authenticated availability but exposes only public club/court
  identity, date/window, and `AVAILABLE`/`UNAVAILABLE`; it must not expose
  booking IDs, customer data, notes, staff, transactions, payment data,
  recurrence context, or internal booking statuses.
- Dashboard summary uses `ClubAccessContext.can_view_dashboard_summary()` and
  `scoped_dashboard_summary_courts_queryset()`. Staff may access this summary
  for their assigned court only.
- Dashboard summary financial visibility is separate from operational access:
  use `ClubAccessContext.can_view_financial_summary()`. Staff receive stable
  response fields with financial values set to `null`.
- Financial dashboard endpoints are overview, revenue, and court utilization.
  Staff cannot access these endpoints.
- Calendar payment summaries, dashboard summary financial totals, and all
  financial dashboard transaction totals include non-cancelled transactions only.
- Dashboard metric names must match their meaning. Do not put counts in fields
  named `amount`.
- Dashboard booking metrics use booking occurrence dates (`Booking.start_time`).
  Period Transaction counts/totals, payments/refunds, payment-method totals,
  settled activity, revenue, and historical Settlement activity use
  `Transaction.created` or the documented historical event date as their date
  authority. Current-custody fields (`unsettled_transaction_count`,
  `unsettled_transaction_total_amount`,
  `staff_with_unsettled_transactions_count`, and `staff_unsettled_money`) are
  all-time state and ignore dashboard date, payment-method, and
  settlement-status activity filters.
  Date-only dashboard ranges mean complete local calendar days with an exclusive
  next-day upper bound. Do not mix booking dates into transaction financial
  filters.
- Dashboard `payment_method_totals` returns every supported payment method with
  signed net `amount`, positive absolute `refund`, and active financial row
  `count`.
- Dashboard Current Custody metrics are derived from the canonical signed
  unsettled candidate rule, not from `Settlement.status=PENDING`. Use
  `staff_with_unsettled_transactions_count` for the distinct collector count;
  do not use `pending_settlement_user_count`. Default collector rows combine all
  accessible Courts; an explicit Court filter narrows them.
- Dashboard views must stay thin and use service functions plus
  `ClubAccessContext`; do not query `ClubMembership` in dashboard views,
  serializers, or services.
- Sprint 8 does not implement advanced pricing, refunds, reversals, exports,
  payment gateway behavior, notifications, marketplace booking, or automatic
  hold expiry jobs.
- Do not create `apps/dashboard/permissions.py` by default. If dashboard
  permissions need a wrapper, keep it centralized in `apps/clubs/permissions.py`.

`apps/reports/`

- Read-only analytics/reporting app. It owns larger business reports that
  combine bookings, courts, working hours, staff, and transactions.
- Current report endpoint is
  `/api/v1/clubs/{club_slug}/reports/court-usage/`.
- Reports have no models or migrations in the current MVP.
- Report constants such as the 31-day maximum, usage statuses, period names,
  evening boundary, and demand bucket size live in `apps/reports/constants.py`.
- Report access is centralized through
  `apps/clubs/access.py -> ClubAccessContext`. Platform admins, owners, and
  managers may access the current court usage report. Staff cannot access it.
- `CanViewClubReports` is a thin wrapper in `apps/clubs/permissions.py`; do not
  create a separate reports permission architecture.
- Court usage filters are `date_from`, `date_to`, optional `court`, `period`,
  custom `hour_from`/`hour_to`, `staff`, and one usage `status`.
- Court usage date ranges are inclusive from the API consumer perspective and
  limited to 31 calendar days.
- Default usage statuses are `CONFIRMED`, `COMPLETED`, and `NO_SHOW`. `HOLD`
  is included only when explicitly requested. `CANCELLED` and `EXPIRED` are not
  accepted for this report.
- Court usage selects bookings by booking time overlap and clips occupied
  minutes to the selected report period. Financial totals count each selected
  booking once and sum non-cancelled attached transactions regardless of
  transaction creation date.
- Peak and low-demand analysis uses fixed 60-minute clock buckets. Low-demand
  output must include zero-demand generated buckets.
- `usage_by_day` occupancy may split across dates, but booking financial totals
  are assigned only to the booking's local start date. Null booking creators are
  labeled as localized `Unknown`.
- Keep report views thin and call report services. Do not query
  `ClubMembership` in report views, serializers, or services; add scoped
  helpers to `ClubAccessContext` when access logic changes.
- Keep report tests with the report app under `apps/reports/tests/`.

## Booking-Native Recurrence

Owning app: `apps/bookings/`. Locked contract:
`docs/recurring-bookings-contract.txt`.

- `Booking.Source.RECURRING` marks a recurring reservation. There is no
  recurring agreement, recurring series, recurring app, rolling-horizon
  generation, action-required state, maintenance command, or dedicated deposit
  ledger. Do not add `apps.recurring` to `INSTALLED_APPS` or URL routing.
- `Booking.recurrence_status` is nullable and uses `ACTIVE`, `RENEWED`, and
  `ENDED`. Non-recurring bookings must keep it null. Only `ACTIVE` recurring
  bookings virtually reserve future weekly slots.
- A recurrence chain uses `Booking.previous_recurring_booking`; the reverse
  relation is `next_recurring_booking`.
- `POST /api/v1/clubs/{club_slug}/bookings/` accepts write-only
  `is_recurring=true`. Clients must not post `source=RECURRING` directly.
- ACTIVE recurrence availability is arithmetic weekly matching by local date
  delta modulo 7 plus interval overlap. Do not generate future booking rows.
- Schedule slots blocked only by a virtual recurrence return
  `slot_status=RECURRING_RESERVED`, `booking=null`,
  `recurring_anchor_booking_id`, and nullable `recurring_context` with
  `anchor_booking_id`, `customer_name`, `customer_phone`, and
  `recurrence_status` from the ACTIVE anchor. Selected slot date/time and
  `slot_price` remain authoritative for the future occurrence; do not treat
  anchor lifecycle or financial fields as the virtual occurrence state.
- Completing an ACTIVE recurring booking requires `continue_recurring`. False
  completes the booking and sets `ENDED`; true atomically creates next week's
  booking, records the next deposit as an ordinary booking `Transaction` when
  required, and marks the completed booking `RENEWED`.
-   `GET /api/v1/clubs/{club_slug}/bookings/{id}/recurrence-next/` is the
  read-only continuation preview for an ACTIVE recurring CONFIRMED booking.
  It returns `can_continue`, `next_start_time`, `next_end_time`,
  `next_total_price`, `next_required_deposit`, and
  `requires_digital_payment_reference` using the same rules as completion.
  `requires_payment_reference` is a deprecated alias of that field. It means
  the court flag `Court.requires_digital_payment_reference`: a reference is
  required only for DIGITAL_WALLET / BANK_TRANSFER, not CASH. It does
  not mutate. Completion revalidates. FE must not send next amounts.
  Stable codes include `BOOKING_RECURRENCE_NOT_ACTIVE`,
  `RECURRENCE_CANNOT_CONTINUE`, and `NEXT_RECURRING_SLOT_UNAVAILABLE`.
- Cancelling, no-showing, or expiring an ACTIVE recurring booking sets
  `recurrence_status=ENDED`. Active recurring bookings cannot be rescheduled;
  end/cancel and create a new recurrence instead.
- Settlements include ordinary signed booking `Transaction` rows only.

## Dashboard, Unsettled Transactions, and Settlement Concepts

### Unsettled transactions are the source of truth

Dashboard settlement-related metrics must be calculated from live unsettled
transactions, not from `Settlement.status = PENDING`.

A transaction is considered unsettled when:

```text
is_cancelled = false
AND transaction is not linked to any settlement line
```

Current Custody is the signed sum of all such authorized Transactions and is
not date-filtered. It includes every payment method and negative REFUND rows.
No candidates and candidates with a zero signed net are different states;
zero-net and negative-net collectors must not disappear.

A settlement record should only be created when an authorized user confirms
settlement.

Settlement preview endpoints must be read-only and must not create settlement
rows, settlement lines, audit logs, locks, or transaction state changes.

### Settlement user naming

Use `collected_by` for the staff/user whose collected money is being settled.

Use `created_by` only for the owner/admin/manager who creates the settlement
record.

Use `settled_by` for the owner/admin/manager who confirms or approves the
settlement.

Do not use `created_by` to mean the staff member being settled in settlement
APIs.

### Staff with unsettled transactions count

Use this field name:

```text
staff_with_unsettled_transactions_count
```

Meaning:

```text
The number of distinct staff/users who currently have at least one unsettled non-cancelled transaction.
```

Do not use names like:

```text
pending_settlement_user_count
```

because the dashboard must not depend on pending settlement records.

### Completed bookings and financial closure

A `COMPLETED` booking should normally be financially closed.

This means:

```text
status = COMPLETED
remaining_amount = 0
```

If a completed booking has `remaining_amount > 0`, treat it as a data integrity
or financial consistency warning, not as a normal operational `needs_action`.

Do not include completed bookings with remaining amount inside the normal
`needs_action_count`.

If needed later, expose a separate field such as:

```text
completed_with_remaining_amount_count
```

or:

```text
data_integrity_warnings_count
```

### Needs action definition

`needs_action_count` means a distinct count of bookings requiring normal
operational attention.

It should include:

```text
1. HOLD bookings waiting for payment.
2. CONFIRMED bookings whose end_time has passed and are not completed.
3. CONFIRMED bookings whose end_time has passed and still have remaining_amount > 0.
4. HOLD bookings close to expiry.
5. EXPIRED bookings only when the request already has explicit date context
   (`date`, `date_from`, or `date_to`) or the dashboard period already
   scopes bookings. Do not invent a 7/14/30-day recency window.
```

It should not include:

```text
COMPLETED bookings with remaining_amount > 0
all historical EXPIRED rows on an unscoped needs_action=true list
```

Unscoped `needs_action=true` therefore still excludes EXPIRED. Including
EXPIRED without date context requires
`NEEDS BUSINESS DECISION — EXPIRED RECENCY WINDOW`.

### Hold expiry logic

Use the existing `internal_hold_expiry_hours` setting to calculate when an
internal hold expires.

Do not hardcode the hold expiry duration.

Recommended logic:

```text
effective_hold_expires_at = min(
    booking.created + court.internal_hold_expiry_hours,
    booking.start_time
)
```

`compute_booking_hold_expires_at()` and `annotate_booking_hold_expires_at()`
are the single authority. Dashboard hold-expiring counts, booking
`hold_expiring`, list/detail `hold_expires_at`, and `expire_hold_bookings`
must use that rule. A HOLD whose start time has arrived is due even if the
policy hours have not elapsed.

`hold_expiring=true` should mean:

```text
status = HOLD
AND hold_expires_at > now
AND hold_expires_at <= now + warning_window
```

The warning window can be a simple MVP default, such as 30 minutes before
expiry, but the actual expiry duration must come from
`internal_hold_expiry_hours`.

### Clickable dashboard cards

Every important dashboard number should map to a list endpoint with matching
filters.

Examples:

```text
transactions?settlement_status=unsettled
bookings?needs_action=true
bookings?needs_action=true&date_from=...&date_to=...
bookings?overdue=true
bookings?has_remaining_amount=true&status=CONFIRMED&ended=true
bookings?hold_expiring=true
settlements/unsettled-summary/
settlements/preview?collected_by=<user_id>
```

`has_remaining_amount=true` means paid amount is less than `total_price`.
`has_remaining_amount=false` means the booking is fully paid. Deprecated
`remaining_amount_gt` remains for compatibility and still returns CONFIRMED
bookings with remaining amount greater than zero.

The same backend logic must be used for dashboard counts and the clicked
filtered lists.

`apps/common/`

- Shared app for reusable infrastructure that is not owned by one business
  domain.
- `apps/common/middleware.py` contains `SQLQueryStatsMiddleware`, a
  production-safe SQL request performance logger. It uses Django's
  `connection.execute_wrapper()` and does not depend on `DEBUG` or
  `connection.queries`. Keep it disabled by default in production and
  tests. When enabled it logs query count, DB time, request time,
  exact duplicate queries, repeated SQL shapes, and a potential N+1
  heuristic logged as `[PERF:N+1?]`. Transaction-control SQL is counted
  but excluded from duplicate/N+1 heuristics. Logs must never include
  SQL parameters, request bodies, or Authorization headers. Verbose
  mode may include truncated SQL structure only.
- Egypt location constants live in `apps/common/egypt_locations.py`.
- Shared customer phone-variant search helpers live in `apps/common/search.py`
  (`phone_search_variants`, `customer_phone_search_q`). Each FilterSet builds
  its own Q: bookings search `customer_name`, `customer_phone`, and `notes`;
  transactions search `booking__customer_name`, `booking__customer_phone`,
  and `payment_reference`.
- `get_governorate_choices()`, `get_all_city_choices()`,
  `get_city_choices(governorate_code)`, and validation helpers are the source
  of truth for controlled governorate and city/center codes.
- New city/center entries should be added to the constants file, not scattered
  in serializers or views.
- Do not add free-form governorate or city fields in future APIs.
- Do not create database tables for locations unless a future task explicitly
  asks for dynamic/admin-managed locations.
- Use shared behavior here only when repeated patterns justify it.
- Base timestamp models are deferred until reusable model behavior exists; do
  not create `apps/common/` only for planned base models.
- Appropriate contents include base models, shared permissions, common viewsets
  or mixins, shared validators, and reusable utilities.

`requirements/`

- `base.txt` contains runtime dependencies.
- `dev.txt` extends `base.txt` and contains test/development tooling.
- Do not introduce new dependencies without explicit approval.

## 6. Development Rules

- Preserve current behavior unless the task explicitly changes it.
- Do not add features while performing architecture, cleanup, or documentation
  tasks.
- Do not refactor unrelated code.
- Keep changes small, focused, and easy to review.
- Prefer existing Django, DRF, and local patterns before introducing new
  abstractions.
- Verify actual app names and import paths before editing settings or URLs.
  Current visible app path is `apps.accounts`.
- Do not copy business-specific concepts from older projects.
- Use generic examples unless the current repository has already implemented a
  concrete domain concept.
- Avoid hidden side effects.
- Avoid signals unless this project intentionally adopts them for a specific,
  documented reason.
- Use `transaction.atomic()` for multi-model writes.
- Use `select_for_update()` when concurrent updates can conflict.
- Do not add club/court ownership, staff assignment, membership, marketplace, or
  booking fields directly to the user table. Club owners, managers, and staff
  must be represented by `ClubMembership`.

## 7. API Rules

- Prefer DRF `ViewSet` classes and standard actions when they fit.
- Use `APIView` only when a ViewSet would make the endpoint less clear.
- Prefer `perform_create()` and `perform_update()` over overriding full
  `create()` or `update()` methods unless the HTTP flow itself must change.
- Use separate serializers for create, update, list, and detail when request or
  response shapes differ.
- Keep response shapes stable once exposed.
- Return DRF `ValidationError` with clear field-keyed messages.
- Do not use arbitrary resource names as `ValidationError` keys just to carry
  a message. Field-level `ValidationError` keys must be real request field
  names. Business-state failures must raise `SlotyAPIException` or a
  domain-specific subclass and return a top-level localized `message` with a
  stable `code`; never return handled error shapes like
  `{"transactions": "..."}`, `{"transaction": "..."}`, or
  `{"booking": "..."}`.
- Use `drf-spectacular` `extend_schema` annotations when automatic schema output
  is weak or ambiguous.
- Do not expose internal-only fields.
- Use pagination consistently for list endpoints.
- Use `django-filter` `FilterSet` classes when filtering becomes non-trivial.
- All public query parameters for list filtering must be declared in the
  domain app's `filters.py`; `FilterSet` classes are the source of truth for
  list-filter parameters.
- ViewSets should declare `filter_backends` and `filterset_class` for list
  filtering instead of parsing filter query params directly.
- `ViewSet.get_queryset()` must return an already authorized/scoped queryset
  before `DjangoFilterBackend` applies request filters.
- `FilterSet` classes must not perform permission checks, import or query
  `ClubMembership`, import `ClubAccessContext`, resolve `club_slug`, or decide
  staff court scope.
- Club-scoped endpoints must not accept a `club` query param because club
  context comes from `/api/v1/clubs/{club_slug}/...`.
- Do not add global success response wrapping. A shared base error format or
  custom exception handler is deferred until a simple, tested need exists.
- Club-scoped business endpoints must verify authenticated user, club slug,
  platform admin or active club membership, role authority, and staff court
  scope through `ClubAccessContext`.

## API Implementation Checklist

Whenever a new API endpoint is added or changed, check whether the following
are needed:

A) URL and versioning

- New public APIs must live under `/api/v1/`.
- Club-scoped APIs must live under `/api/v1/clubs/{club_slug}/...`.
- Do not create unversioned `/api/...` routes for new public APIs.
- Do not create global business endpoints when the business context is
  club-scoped.

B) Access/scoping

- Use `ClubAccessContext` for club-scoped access.
- `ViewSet.get_queryset()` must return an already scoped queryset.
- Do not trust frontend-selected club/court/token claims without DB
  verification.
- Do not query `ClubMembership` directly outside centralized access code unless
  the task explicitly updates that access layer.
- Do not create new per-app permission classes unless clearly needed and
  approved.

C) Services

- Put workflow, state-change, and multi-model write logic in services.
- Use `transaction.atomic()` for multi-model writes.
- Use `select_for_update()` when concurrent changes can conflict.

D) Filters

- Non-trivial list filters must use django-filter `FilterSet` classes.
- Public query params must be explicitly declared in
  `apps/<domain>/filters.py`.
- ViewSets should use `DjangoFilterBackend` and `filterset_class`.
- Filters must not contain permission logic.
- Filters must not import `ClubAccessContext` or query `ClubMembership`.

E) Serializers

- Use separate create, update, list, and detail serializers when request and
  response shapes differ.
- Do not expose internal fields.
- Keep response shapes stable.

F) Schema/docs

- Ensure the endpoint appears in `/api/v1/schema/` and `/api/v1/docs/`.
- Use `extend_schema` only when automatic schema output is weak or ambiguous.
- Keep `/api/v1/docs/` and `/api/v1/schema/` public for development.

G) Tests

- Add tests for authentication.
- Add tests for unauthorized access.
- Add tests for authorized access.
- Add tests for role/court/club scoping.
- Add tests for invalid input.
- Add tests for filters if list filters exist.
- Add regression tests for security-sensitive behavior.

H) Seed data

- Update `seed_demo_data` when a new endpoint needs realistic manual testing
  data.
- Seed data must be idempotent.
- Seed data must use management commands, not migrations.
- Seed data must not create future sprint concepts before they exist.
- Use predictable usernames, slugs, and references.

I) Documentation

- Update `README.md` when a public endpoint is added or changed.
- Update `AGENTS.md` when architecture, conventions, or workflow changes.
- Keep docs concise and useful for future Codex runs.

J) Strict boundaries

- Do not implement future sprint features during endpoint work.
- Do not add dependencies without approval.
- Do not add fields to `User` for club/court roles.
- Do not reintroduce `CourtStaffAssignment`.
- Do not use “tenant” terminology.

## 8. Authentication and Permission Rules

- Authentication and authorization must be explicit.
- Do not assume default permissions are enough for sensitive endpoints.
- Put domain-specific permission classes in the owning app's `permissions.py`
  when needed.
- Future transaction endpoints are the exception to the default permission-file
  pattern: do not add transaction-specific DRF permission classes unless
  absolutely necessary, and explain the need before adding one.
- Shared permission helpers may move to `apps/common/` only after reuse exists.
- Tests for protected endpoints must cover unauthenticated, unauthorized, and
  authorized cases where applicable.
- If JWT authentication is used, keep token parsing/authentication behavior
  isolated from business services.

## 9. Business Logic Rules

- Keep views thin.
- Do not put complex business logic in views.
- Do not put risky state transitions directly in serializers.
- Use validators for reusable domain rules.
- Use services for workflows, state changes, orchestration, and multi-model
  writes.
- Tasks should call services and should not contain core business logic.
- Models may enforce lightweight invariants, but workflow decisions belong in
  services.
- Make state transitions explicit and test them at the service layer.
- Do not invent business rules that are not requested or already documented in
  implemented code.
- Sprint 1 role helpers and role permissions are foundation only; do not use
  them to imply club/court access rules. Club/court authority now belongs to
  `ClubMembership`.

## 10. Database and Query Rules

- Use Django ORM idioms.
- All schema changes require migrations.
- Keep migration names descriptive.
- Add database constraints and indexes for important invariants.
- Use `select_related()` and `prefetch_related()` for list/detail endpoints that
  would otherwise cause N+1 queries.
- Use `select_for_update()` inside transactions for conflicting concurrent
  updates.
- Avoid data changes inside schema migrations unless necessary and clearly
  documented.
- Prefer explicit queryset scoping in views or managers over filtering in
  serializers.

## 11. Testing Rules

- Use the existing test framework found in the repo.
- `requirements/dev.txt` includes `pytest`, `pytest-django`, `factory-boy`,
  `pytest-mock`, and related tooling, so pytest-style tests are allowed when the
  project config supports them.
- Tests currently live in a top-level pytest layout under `tests/`.
- `tests/accounts/test_user_model.py` contains Sprint 1 model and permission
  tests.
- Current API tests include `tests/test_api_foundation_urls.py`,
  `tests/accounts/test_account_api.py`, `tests/clubs/test_club_api.py`,
  `tests/courts/test_court_api.py`, and `tests/bookings/test_booking_api.py`.
- Add future tests under `tests/<domain>/` unless the project intentionally
  adopts app-local tests for a specific reason.
- Test services for business workflows and state transitions.
- Test serializers for input validation.
- Test API endpoints for permissions, request shape, response shape, and query
  behavior.
- Add regression tests when fixing bugs.
- Run targeted tests first, then broader tests when risk justifies it.

## 12. Code Quality Rules

- Follow existing formatting and linting tools.
- Use the root `.pre-commit-config.yaml`; do not place pre-commit config inside
  apps or nested folders.
- Black and isort are configured in `pyproject.toml`.
- Flake8 is configured in `setup.cfg`.
- Keep dev-only tooling dependencies in `requirements/dev.txt`; do not add
  formatter, linter, or pre-commit dependencies to `requirements/base.txt`.
- Run `pre-commit run --all-files` after structural or broad formatting changes
  when practical.
- Do not introduce new tooling without explicit approval.
- Keep comments short and useful.
- Avoid broad rewrites that are not required by the task.
- Leave unrelated files untouched.
- Do not edit secrets or depend on values from `.env` in documentation.

## 13. Commands

Install dependencies:

```bash
python -m pip install -r requirements/dev.txt
```

If using the checked-in virtual environment, run commands through the active
environment or call `.venv/bin/python` directly.

Run Django checks:

```bash
python manage.py check
```

Run the development server:

```bash
python manage.py runserver
```

Create migrations:

```bash
python manage.py makemigrations
```

Apply migrations:

```bash
python manage.py migrate
```

Run targeted tests:

```bash
pytest tests/accounts
```

Run Sprint 2 setup tests:

```bash
pytest tests/clubs tests/courts
```

Run Sprint 3 booking tests:

```bash
pytest tests/bookings
```

Run Sprint 4 transaction tests:

```bash
pytest tests/transactions
```

Run Sprint 6 settlement tests:

```bash
pytest tests/settlements
```

Run Sprint 7 audit tests:

```bash
pytest tests/audit
```

Run Sprint 8 dashboard tests:

```bash
pytest tests/dashboard
```

Seed local/demo data for manual endpoint testing:

```bash
python manage.py seed_demo_data
```

Run all tests:

```bash
pytest
```

Run Django's built-in test runner if pytest is not configured for the current
environment yet:

```bash
python manage.py test
```

Install pre-commit hooks:

```bash
pre-commit install
```

Run pre-commit on all files:

```bash
pre-commit run --all-files
```

Notes:

- Activate a virtual environment and install `requirements/dev.txt` before
  running Django or pytest commands.
- The default `manage.py`, ASGI, and WSGI settings module is
  `config.settings.local`.
- Pytest is configured through `pyproject.toml` to use `config.settings.test`.
- `SECRET_KEY` must be provided through the environment or local `.env`.
- JWT access and refresh lifetimes are environment-driven through
  `JWT_ACCESS_TOKEN_MINUTES` (default 60) and `JWT_REFRESH_TOKEN_DAYS`
  (default 7). Custom JWT claims are unchanged. A 12-hour working shift is
  supported when the frontend silently refreshes; production must not set
  `JWT_REFRESH_TOKEN_DAYS` to an incompatible short value. Do not change
  refresh lifetime to exactly 12 hours.
- Business calendar rules use Django `TIME_ZONE = "Africa/Cairo"` with
  `USE_TZ = True`. Datetimes remain timezone-aware UTC in the database.
- SQLite is the default local database. Set `DB_ENGINE=postgresql` plus the
  `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` variables when
  using PostgreSQL.
- PostgreSQL concurrency tests live in `tests/test_postgresql_concurrency.py`
  and skip on SQLite. Run them with:
  `DB_ENGINE=postgresql pytest tests/test_postgresql_concurrency.py`
- If settings are changed, verify the configured local apps match real package
  paths under `apps/`.
- Club-scoped API routes currently include:
  `/api/v1/clubs/`,
  `/api/v1/clubs/{club_slug}/memberships/`,
  `/api/v1/clubs/{club_slug}/courts/`,
  `/api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`,
  `/api/v1/clubs/{club_slug}/court-working-hours/` (deprecated compatibility),
  `/api/v1/clubs/{club_slug}/bookings/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/cancel/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/complete/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/no-show/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/reschedule/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/expire/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/end-recurrence/`,
  `/api/v1/clubs/{club_slug}/bookings/{id}/recurrence-next/`,
  `/api/v1/clubs/{club_slug}/transactions/`,
  `/api/v1/clubs/{club_slug}/transactions/{id}/cancel/`,
  `/api/v1/clubs/{club_slug}/settlements/`,
  `/api/v1/clubs/{club_slug}/settlements/preview/`,
  `/api/v1/clubs/{club_slug}/settlements/unsettled-summary/`, and
  `/api/v1/clubs/{club_slug}/settlements/{id}/mark-settled/`,
  `/api/v1/clubs/{club_slug}/audit-logs/`, and
  `/api/v1/clubs/{club_slug}/audit-logs/{id}/`.
- Sprint 8 read-only dashboard routes include:
  `/api/v1/clubs/{club_slug}/courts/{court_id}/availability/`,
  `/api/v1/clubs/{club_slug}/calendar/`,
  `/api/v1/clubs/{club_slug}/dashboard/overview/`,
  `/api/v1/clubs/{club_slug}/dashboard/summary/`,
  `/api/v1/clubs/{club_slug}/dashboard/revenue/`, and
  `/api/v1/clubs/{club_slug}/dashboard/court-utilization/`.
- Global API routes currently include `/api/v1/me/`, `/api/v1/users/`,
  `/api/v1/egypt-locations/`, `/api/v1/auth/token/`,
  `/api/v1/auth/token/refresh/`, `/api/v1/schema/`, and `/api/v1/docs/`.

## 14. Demo Seed Data Pattern

- New endpoint work should consider whether demo seed data is needed for
  manual Swagger/Postman testing.
- Demo seed data must be added through an idempotent management command, not
  migrations.
- Current command: `python manage.py seed_demo_data`.
- Scoped Albaladya frontend/local testing data is seeded with
  `python manage.py seed_demo_data --scenario albaladya-test`. It creates the
  `albaladya-test` club, one court, deterministic `admin`/`owner`/`manager`/
  `staff` users, membership-level manager permissions, and weekly pricing.
  Its shared password is development/test-only and must never be production
  credentials.
- Seed data is for local/manual testing only and must not create production
  side effects.
- Use predictable usernames, slugs, dates, and references.
- Default demo seed data uses marketing-friendly football club and stadium
  labels such as Barcelona FC, Real Madrid CF, Liverpool FC, Spotify Camp Nou,
  Santiago Bernabeu, and Anfield while keeping internal A/B/C seed keys stable.
- Maintain role-specific and club-specific demo users for multi-club testing.
- Do not reuse active manager or staff demo users across clubs because current
  membership constraints allow only one active manager or staff assignment per
  user.
- Keep seed data idempotent, non-destructive, and aligned with currently
  implemented endpoints.
- New API work should update `seed_demo_data` only when manual testing needs
  new realistic data.
- Do not seed future sprint concepts before they exist.
- Do not add fixtures unless the project intentionally adopts fixtures.

## 15. Do and Don't Rules for Codex Agents

Do:

- Read this file before planning work.
- Inspect the current repo structure before making architecture decisions.
- Update this file when conventions change.
- Keep the backend as a Django + DRF modular monolith.
- Put new domain behavior under `apps/<domain>/`.
- Keep views thin and use services for workflows.
- Use validators for reusable business rules.
- Use migrations for schema changes.
- Add focused tests for changed behavior.
- Run the smallest useful verification command before broader checks.
- Clearly label planned patterns when files or apps do not exist yet.

Don't:

- Do not copy old project business logic or domain-specific concepts.
- Do not invent features or business rules.
- Do not refactor unrelated code.
- Do not change business logic unless explicitly requested.
- Do not hide workflows inside serializers, tasks, or signals.
- Do not add dependencies or tools without approval.
- Do not expose internal fields through API serializers.
- Do not assume authentication or authorization is handled elsewhere.
- Do not pretend planned folders exist before creating them.
- Do not modify files outside the requested scope.
