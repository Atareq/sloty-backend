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
- Current implemented domain apps: `apps/clubs/`, `apps/courts/`, and
  `apps/bookings/`
- Project docs live under `docs/`
- Requirements are split into `requirements/base.txt` and `requirements/dev.txt`
- Style tooling exists through `.pre-commit-config.yaml`, `pyproject.toml`, and
  `setup.cfg`
- Sprint 1 implements backend foundation and the custom accounts user model
- Sprint 2 implements club/court setup, club membership assignment, and setup
  API scoping
- Sprint 3 implements booking creation, booking list/detail and schedule
  filters, and application-level overlap protection
- Planned shared app name is `apps/common/`
- Domain apps beyond `accounts`, `clubs`, `courts`, and `bookings` are not
  implemented yet

Planned project direction:

- Treat the backend as a Django + DRF modular monolith.
- Add each business area as a focused Django app under `apps/<domain>/`.
- Keep business workflows in service modules and keep HTTP views thin.
- Add shared infrastructure only when repeated patterns justify it.
- Planned app layout: `accounts`, `clubs`, `courts`, `bookings`,
  `transactions`, `settlements`, `pricing`, `audit`, and `common`.
- Only `accounts`, `clubs`, `courts`, and `bookings` should exist through
  Sprint 3.

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

- `apps/accounts/` contains the custom user model, identity fields, platform
  authority flag, account admin registration, and account permission helpers.
- `User` is identity plus platform authority only. It has
  `is_platform_admin`; it does not store club-scoped OWNER, MANAGER, or STAFF
  roles.
- It must not contain club, court, booking, transaction, settlement, pricing,
  staff shift, marketplace, or assignment business logic.
- `apps/clubs/` contains club setup, club membership assignment logic, and the
  central `ClubAccessContext` club-scoped access layer.
- `Club` has a unique `slug` used by club-scoped business API URLs.
- `ClubMembership` is the single source of OWNER, MANAGER, and STAFF authority
  inside a club. STAFF memberships are tied to a court through
  `ClubMembership.court`.
- `apps/courts/` contains court setup and court working hours logic.
- Club/court scope must come from active `ClubMembership` rows, not from direct
  club or court fields on `User`.
- `apps/bookings/` contains booking creation, list/detail APIs, schedule-style
  filters, price snapshot calculation, and active booking overlap protection.
- Booking outside working hours is allowed in Sprint 3, and no
  `outside_working_hours` flag is stored.
- Transactions, booking lifecycle actions, settlements, dashboards, and audit
  logs are future sprint work and must not be implemented in `bookings` during
  Sprint 3.
- Business APIs for memberships, courts, working hours, and bookings are
  club-scoped under `/api/clubs/{club_slug}/...`.
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
- Contains the custom `User` model, `is_platform_admin`, account admin
  registration, and account permission helpers.
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
- `apps/clubs/access.py` contains `ClubAccessContext`, the central source of
  truth for club-scoped access checks and scoped querysets.
- `apps/clubs/mixins.py` contains `ClubScopedAccessMixin` for club-scoped
  ViewSets.
- Do not place court, booking, transaction, settlement, pricing, or audit
  behavior here.

`apps/courts/`

- Court setup app.
- Contains `Court` and `CourtWorkingHour`.
- `CourtStaffAssignment` has been removed. Staff access is represented by
  `ClubMembership(role=STAFF, court=<court>)`.
- Do not place booking, transaction, settlement, pricing, or audit behavior
  here.

`apps/bookings/`

- Booking foundation app.
- Contains `Booking`, booking serializers, scoped booking viewsets, and booking
  creation services.
- New bookings start as `HOLD` because transactions are not implemented until
  Sprint 4.
- `total_price` is calculated by the backend from the court default price and
  slot duration; clients must not control booking price in Sprint 3.
- Overlap protection is currently application-level: `HOLD` and `CONFIRMED`
  bookings block overlapping bookings on the same court.
- Booking access is club-scoped through `ClubAccessContext`. Staff users can
  list and create bookings only for their assigned court.
- Do not place transaction, settlement, lifecycle action, dashboard, marketplace,
  notification, or audit-log behavior here in Sprint 3.

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
  `/api/clubs/{club_slug}/memberships/`,
  `/api/clubs/{club_slug}/courts/`,
  `/api/clubs/{club_slug}/court-working-hours/`, and
  `/api/clubs/{club_slug}/bookings/`.

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
