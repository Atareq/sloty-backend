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
  `apps/bookings/`, and `apps/transactions/`
- Current API foundation endpoints include `/api/schema/`, `/api/docs/`,
  `/api/auth/token/`, and `/api/auth/token/refresh/`
- Current account endpoints include `/api/me/` and platform-admin-only
  `/api/users/`
- Project docs live under `docs/`
- Requirements are split into `requirements/base.txt` and `requirements/dev.txt`
- Style tooling exists through `.pre-commit-config.yaml`, `pyproject.toml`, and
  `setup.cfg`
- Sprint 1 implements backend foundation, JWT login/refresh URLs, the custom
  accounts user model, `/api/me/`, and platform-admin user management APIs
- Sprint 2 implements club/court setup, club membership assignment, and setup
  API scoping
- Sprint 3 implements booking creation, booking list/detail and schedule
  filters, and application-level overlap protection
- Sprint 4 implements immutable booking transaction recording, booking payment
  summaries, and HOLD-to-CONFIRMED booking confirmation after the first valid
  transaction
- Planned shared app name is `apps/common/`
- Domain apps beyond `accounts`, `clubs`, `courts`, `bookings`, and
  `transactions` are not implemented yet

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
  They may manage identity fields, `is_active`, and `is_platform_admin`; they
  must not expose or accept Django `is_staff`, `is_superuser`, or passwords in
  read responses.
- It must not contain club, court, booking, transaction, settlement, pricing,
  staff shift, marketplace, or assignment business logic.
- `apps/clubs/` contains club setup, club membership assignment logic, and the
  central `ClubAccessContext` club-scoped access layer.
- `Club` has a unique `slug` used by club-scoped business API URLs.
- `Club` stores `manager_can_settle_transactions` and
  `manager_can_change_pricing` flags. `manager_can_change_pricing` currently
  gates manager updates to a court's `default_price`. Settlement behavior is
  still future work even though the flag exists.
- `ClubMembership` is the single source of OWNER, MANAGER, and STAFF authority
  inside a club. STAFF memberships are tied to a court through
  `ClubMembership.court`.
- `apps/courts/` contains court setup and court working hours logic.
- `Court` stores `default_price`, `slot_duration_minutes`,
  `requires_digital_payment_reference`, and `internal_hold_expiry_hours`.
  Sprint 4 transactions use `requires_digital_payment_reference` for manual
  payment-reference validation. Hold-expiry behavior remains future lifecycle
  work and must not be implemented in Sprint 4.
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
- Booking lifecycle actions, settlements, dashboards, and audit logs are future
  sprint work and must not be implemented in `bookings` during Sprint 4.
- `apps/transactions/` contains immutable booking transaction recording,
  transaction create/list/detail APIs, payment reference uniqueness inside a
  club, and booking payment summary support.
- Transaction creation confirms a HOLD booking to CONFIRMED. Other booking
  lifecycle actions remain future work.
- Settlements, corrections, refunds, reversals, dashboards, reports, audit logs,
  online payment gateway logic, and platform commission calculation remain
  future work.
- Business APIs for memberships, courts, working hours, bookings, and
  transactions are club-scoped under `/api/clubs/{club_slug}/...`.
- Login remains global. The frontend logs in, calls `/api/me/` to read active
  memberships and club slugs, then sends selected-club requests to
  `/api/clubs/{club_slug}/...`. Never trust frontend-selected club context
  without backend verification.
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
  `/api/me/`, platform-admin-only `/api/users/`, and account permission helpers.
- `User.is_platform_super_admin()` is a temporary compatibility helper that
  returns `is_platform_admin`.
- Do not add or reintroduce club-scoped business roles on `User`.
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
- `apps/clubs/access.py` contains `ClubAccessContext`, the central source of
  truth for club-scoped access checks and scoped querysets.
- `apps/clubs/mixins.py` contains `ClubScopedAccessMixin` for club-scoped
  ViewSets.
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
- `CourtStaffAssignment` has been removed. Staff access is represented by
  `ClubMembership(role=STAFF, court=<court>)`.
- Do not place booking, transaction, settlement, pricing, or audit behavior
  here.

`apps/bookings/`

- Booking foundation app.
- Contains `Booking`, booking serializers, scoped booking viewsets, and booking
  creation services.
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
- Do not place transaction creation logic, settlement, lifecycle action,
  dashboard, marketplace, notification, or audit-log behavior in `bookings`.

`apps/transactions/`

- Sprint 4 transaction recording app.
- Contains `Transaction`, transaction serializers, transaction viewsets,
  transaction creation services, and transaction admin registration.
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
- Do not duplicate `ClubMembership` queries inside transaction views,
  serializers, or services.
- Do not create independent per-app transaction permission logic. If a
  transaction-specific DRF permission class seems absolutely necessary, stop
  and explain why before adding it.
- Transactions are immutable through the API: no PATCH, PUT, DELETE, void,
  correction, refund, reversal, or settlement endpoint exists in Sprint 4.
- Transaction creation may change booking status only from HOLD to CONFIRMED.
  Do not implement other booking lifecycle actions in Sprint 4.
- `manager_can_settle_transactions` remains reserved for future settlement
  behavior and must not control Sprint 4 payment recording.

`apps/common/`

- Planned pattern, not currently present.
- Use only when shared behavior exists.
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
- Do not add global success response wrapping. A shared base error format or
  custom exception handler is deferred until a simple, tested need exists.
- Club-scoped business endpoints must verify authenticated user, club slug,
  platform admin or active club membership, role authority, and staff court
  scope through `ClubAccessContext`.

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
  `/api/clubs/`,
  `/api/clubs/{club_slug}/memberships/`,
  `/api/clubs/{club_slug}/courts/`,
  `/api/clubs/{club_slug}/court-working-hours/`, and
  `/api/clubs/{club_slug}/bookings/`,
  `/api/clubs/{club_slug}/transactions/`.
- Global API routes currently include `/api/me/`, `/api/users/`,
  `/api/auth/token/`, `/api/auth/token/refresh/`, `/api/schema/`, and
  `/api/docs/`.

## 14. Do and Don't Rules for Codex Agents

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
