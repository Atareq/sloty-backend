# AGENTS.md

## 1. Purpose

This file is the living guide for AI coding agents working in this repository.
Always read it before planning commands or code changes. Also read
`docs/business-analysis.txt` and `docs/documentation.txt`, when present, for
project and product context before planning changes. Update this file whenever
the repository's backend structure, conventions, or workflow changes.

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
  `apps/audit/`, and `apps/dashboard/`
- Current shared app: `apps/common/`
- Current public API routes are versioned under `/api/v1/`
- Current API foundation endpoints include `/api/v1/schema/`,
  `/api/v1/docs/`, `/api/v1/auth/token/`, and
  `/api/v1/auth/token/refresh/`
- Current public utility endpoints include `/api/v1/egypt-locations/`
- Current account endpoints include `/api/v1/me/` and platform-admin-only
  `/api/v1/users/`
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
  rescheduling, remaining-cash completion confirmation, automatic hold-expiry
  command support, and lifecycle audit logs
- Sprint 10 adds logged transaction cancelling for safe corrections, excludes
  cancelled transactions from financial totals and settlements, and recalculates
  booking payment status from valid transactions
- Planned shared app name is `apps/common/`
- Domain apps beyond `accounts`, `clubs`, `courts`, `bookings`,
  `transactions`, `settlements`, `audit`, and `dashboard` are not implemented
  yet

Planned project direction:

- Treat the backend as a Django + DRF modular monolith.
- Add each business area as a focused Django app under `apps/<domain>/`.
- Keep business workflows in service modules and keep HTTP views thin.
- Add shared infrastructure only when repeated patterns justify it.
- Planned app layout: `accounts`, `clubs`, `courts`, `bookings`,
  `transactions`, `settlements`, `pricing`, `audit`, and `common`.
- Only `accounts`, `clubs`, `courts`, and `bookings` should exist through
  Sprint 3. Sprint 4 adds `transactions`.

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
- `Club` stores `manager_can_settle_transactions` and
  `manager_can_change_pricing` flags. `manager_can_change_pricing` currently
  gates manager updates to a court's `default_price`.
  `manager_can_settle_transactions` gates manager settlement access in
  Sprint 6.
- `ClubMembership` is the single source of OWNER, MANAGER, and STAFF authority
  inside a club. STAFF memberships are tied to a court through
  `ClubMembership.court`.
- `POST /api/v1/clubs/{club_slug}/memberships/` supports club-scoped onboarding:
  nested user data plus membership role/court are persisted together through
  `apps/clubs/services.py`.
- `apps/courts/` contains court setup and court working hours logic.
- `Court` stores `default_price`, `slot_duration_minutes`,
  `requires_digital_payment_reference`, and `internal_hold_expiry_hours`.
  Sprint 4 transactions use `requires_digital_payment_reference` for manual
  payment-reference validation. Hold-expiry behavior remains future lifecycle
  work and must not be implemented in Sprint 4.
- Court working hours are court-scoped under
  `/api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`. They remain one
  `CourtWorkingHour` row per court plus weekday; do not move working-hour fields
  onto `Court` or convert them to one-to-one settings.
- Club/court scope must come from active `ClubMembership` rows, not from direct
  club or court fields on `User`.
- `apps/bookings/` contains booking creation, list/detail APIs, schedule-style
  filters, price snapshot calculation, and active booking overlap protection.
- Current booking source values are `MANUAL` and `ADMIN_CORRECTION`; only a
  Platform Super Admin may create an `ADMIN_CORRECTION` booking.
- Sprint 3 booking PATCH only changes `customer_name`, `customer_phone`, and
  `notes` on non-locked bookings. It must not change court, start/end time,
  status, source, or price.
- Booking list filters currently supported by the API are `court`, `status`,
  `source`, `date`, `date_from`, and `date_to`.
- Booking outside working hours is allowed in Sprint 3, and no
  `outside_working_hours` flag is stored.
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
- `docs/business-analysis.txt` and `docs/documentation.txt` are required
  overview references before planning code changes when they exist.
- `docs/sprints.txt` tracks sprint planning context.

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
- Contains `Court` and `CourtWorkingHour`.
- Platform admins and club owners can create and update courts. Managers can
  update only `default_price`, and only when the selected club has
  `manager_can_change_pricing=True`. Staff can list/retrieve only their
  assigned court and cannot update courts.
- Platform admins, owners, and managers can manage working hours for accessible
  courts. Staff can list working hours for their assigned court only.
- The nested court working-hours endpoint is the primary API for frontend
  settings pages. The older `/court-working-hours/` route is compatibility-only.
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
- `total_price` is calculated by the backend from the court default price and
  slot duration; clients must not control booking price in Sprint 3.
- Overlap protection is currently application-level: `HOLD` and `CONFIRMED`
  bookings block overlapping bookings on the same court.
- Booking access is club-scoped through `ClubAccessContext`. Staff users can
  list and create bookings only for their assigned court.
- Creating bookings on inactive clubs or inactive courts is rejected.
- `COMPLETED`, `CANCELLED`, `NO_SHOW`, and `EXPIRED` bookings are treated as
  locked for Sprint 3 update behavior and do not block new overlapping slots.
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
  staff must provide a non-empty cancellation reason.
- `no-show` is allowed only from `CONFIRMED` and stores an optional reason.
- `reschedule` is allowed only from `HOLD` or `CONFIRMED`; the new court must
  belong to the selected club, pass `ClubAccessContext` access checks, match the
  court slot duration, and avoid overlaps with other `HOLD` or `CONFIRMED`
  bookings while excluding the current booking.
- Rescheduling keeps existing transactions attached to the same booking. If the
  recalculated price is higher, update `total_price`; if it is lower or equal,
  keep the existing `total_price`.
- `complete` is allowed only from `CONFIRMED`. If a dynamic remaining amount
  exists, the request must set `confirm_collect_remaining_cash=true`; the
  service then creates a CASH transaction for the remaining amount before
  marking the booking `COMPLETED`.
- Manual `expire` is allowed only from `HOLD`. Automatic due-hold expiry uses
  `python manage.py expire_hold_bookings`, sets `EXPIRED`, records
  `expired_at`, and audits with actor `None`.
- Booking lifecycle traceability fields are `cancellation_reason`,
  `no_show_reason`, `reschedule_reason`, `completed_at`, `cancelled_at`,
  `no_show_at`, and `expired_at`. Do not add payment status or cached remaining
  amount fields.
- Do not place transaction creation logic, settlement, rescheduling,
  dashboard, marketplace, or notification behavior in `bookings`, except the
  Sprint 9 remaining-cash completion transaction and hold-expiry command
  explicitly owned by booking lifecycle services.

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
  list/detail scoping.
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
  DELETE, refund, or reversal behavior exists. Corrections use
  `POST .../transactions/{id}/cancel/` with a required reason, followed by normal
  transaction creation.
- Cancelled transactions remain visible and may be filtered with `is_cancelled`, but
  only non-cancelled transactions count toward paid/remaining amounts, completion
  collection, settlements, calendar summaries, and dashboard revenue.
- Already cancelled or settled transactions and transactions attached to terminal
  bookings cannot be cancelled. If no valid payment remains on a confirmed
  booking, the cancel service returns the booking to `HOLD` in the same atomic,
  row-locked workflow.
- Transaction creation may change booking status only from HOLD to CONFIRMED.
  Transaction cancel may change booking status only from CONFIRMED to HOLD.

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
- Platform admins and owners can preview, create, list, retrieve, and mark
  settlements as settled in the selected club.
- Managers can access settlements only when
  `club.manager_can_settle_transactions=True`.
- Staff cannot access settlement endpoints in Sprint 6.
- `SettlementTransaction.transaction` is a `OneToOneField` to `Transaction`;
  this prevents one transaction from being included in more than one
  settlement while keeping transaction rows immutable.
- Settlements include non-cancelled already-recorded transactions by club,
  optional court, period, and unsettled state. Booking lifecycle status is not
  used to decide settlement inclusion in Sprint 6.
- Settlements do not implement refunds, reversals, corrections, commission,
  payout automation, dashboards, or automatic settlement jobs.
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
- Audited Sprint 7 actions are booking create/update/lifecycle transitions,
  transaction creation, settlement creation, and mark-settled.
- Sprint 9 adds `BOOKING_RESCHEDULED` and requires audit logs for cancellation,
  no-show, reschedule, completion, manual expiry, automatic expiry, and auto
  cash transaction creation on completion.
- Sprint 10 adds `TRANSACTION_CANCELLED`. A cancel records before/after cancel and
  booking status data plus reason metadata; a CONFIRMED-to-HOLD recalculation
  also records an explicit `BOOKING_UPDATED` audit log.
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
- Dashboard views must stay thin and use service functions plus
  `ClubAccessContext`; do not query `ClubMembership` in dashboard views,
  serializers, or services.
- Sprint 8 does not implement advanced pricing, refunds, reversals, exports,
  payment gateway behavior, notifications, marketplace booking, or automatic
  hold expiry jobs.
- Do not create `apps/dashboard/permissions.py` by default. If dashboard
  permissions need a wrapper, keep it centralized in `apps/clubs/permissions.py`.

`apps/common/`

- Shared app for reusable infrastructure that is not owned by one business
  domain.
- Egypt location constants live in `apps/common/egypt_locations.py`.
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
- SQLite is the default local database. Set `DB_ENGINE=postgresql` plus the
  `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` variables when
  using PostgreSQL.
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
  `/api/v1/clubs/{club_slug}/transactions/`,
  `/api/v1/clubs/{club_slug}/transactions/{id}/cancel/`,
  `/api/v1/clubs/{club_slug}/settlements/`,
  `/api/v1/clubs/{club_slug}/settlements/preview/`, and
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
- Seed data is for local/manual testing only and must not create production
  side effects.
- Use predictable usernames, slugs, dates, and references.
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
