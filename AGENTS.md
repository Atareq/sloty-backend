# AGENTS.md — Global Guide & Routing

## 1. Purpose & Project Identity

This file is the root engineering guide and documentation router for AI coding agents and developers working in `sloty-backend`.

**Sloty** is a sports court rental management system (starting with football courts in Assiut and Upper Egypt). The backend is built with Django and Django REST Framework (DRF) as a modular monolith.

---

## 2. Source-of-Truth Hierarchy

When evaluating requirements, invariants, or conflicting instructions, resolve them using this strict hierarchy:

```text
Code + Database Models
        ↓
Tests
        ↓
Approved Product/API Contracts (under docs/)
  including docs/architecture/security-architecture-v1.md (target only)
        ↓
Scoped Domain AGENTS.md (under apps/<domain>/)
        ↓
Root AGENTS.md
```

**Rule**: Code and tests are authoritative for **active behavior**. If any `AGENTS.md` or architecture reference conflicts with actual implementation, **the code wins**. Architecture docs under `docs/architecture/` describe the **future target and migration direction** — do not force implementation changes solely to match them. Update living guides to reflect reality rather than introducing breaking code changes or assumptions.

---

## 3. Documentation Routing

Domain-specific business rules, models, invariants, and workflows live inside scoped domain guides. **Read the corresponding scoped guide before modifying code in any domain app:**

| Domain / App | Path | Primary Ownership |
| :--- | :--- | :--- |
| **Accounts** | [`apps/accounts/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/AGENTS.md) | Authentication identity (`User`), platform admin authority, auth endpoints, orphan diagnostics. |
| **Players** | [`apps/players/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/AGENTS.md) | Global `PlayerProfile` + club-local `ClubPlayer` customer identity (see [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md)). |
| **Clubs** | [`apps/clubs/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/AGENTS.md) | Club tenant definitions, `ClubMembership` operational actor lifecycle, location validation, legacy access engine. |
| **Courts** | [`apps/courts/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/AGENTS.md) | Court settings, working hours, pricing periods, midnight/slot time semantics. |
| **Bookings** | [`apps/bookings/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/AGENTS.md) | Booking lifecycle, agreed price snapshots, overlap protection, recurrence, hold expiry (see [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)). |
| **Transactions** | [`apps/transactions/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/AGENTS.md) | Immutable financial ledger, payment thresholds, cancellation/correction workflow. |
| **Settlements** | [`apps/settlements/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/AGENTS.md) | Cash closing, Current Custody (Club + Collector), preview/settle workflows. |
| **Audit** | [`apps/audit/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/AGENTS.md) | Append-only activity trail, stable machine actions, event snapshot architecture. |
| **Dashboard** | [`apps/dashboard/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/AGENTS.md) | Operational vs. financial metrics, calendar, public availability contract. |
| **Reports** | [`apps/reports/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/AGENTS.md) | Analytical reports, court usage metrics, 31-day window, occupancy clipping. |
| **Common** | [`apps/common/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/AGENTS.md) | Egypt locations, standardized error handling (`SlotyAPIException`), Authorization Spine, search, middleware. |
| **Security Architecture (target)** | [`docs/architecture/security-architecture-v1.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md) | Long-term identity/auth/authorization target and migration guideline (not active-behavior authority). |

---

## 4. Architecture & Request Flow

The repository follows a modular-monolith pattern:
- Apps live under `apps/<domain>/`.
- Business workflows belong in service modules (`services.py`). Views must remain thin.
- Cross-domain calls must be explicit function calls through service interfaces; **do not use Django signals or implicit imports**.

### Preferred Request Flow
```text
URL Routing → ViewSet/APIView → Serializer Validation → Service / Validator → ORM Models → Response Serializer
```

- **URLs**: Route requests and extract URL parameters (e.g. `club_slug`). Prefer exposing security boundaries in the URL when those boundaries are required (see [`security-architecture-v1.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md) §13).
- **Views**: Handle HTTP status, permissions, serializer selection, and queryset scoping.
- **Serializers**: Validate request payload shape and format response representation. The code, serializers, and OpenAPI schemas (`/api/v1/schema/`) are the authoritative contract for request/response shapes.
- **Services**: Own transactions, domain state machines, concurrency locks, multi-model writes, and audit recording. Services must **not** normally branch on `role == ...` or re-check club/court access — that belongs to the Authorization Spine / domain authorization boundary.
- **Models**: Define database constraints, schema invariants, indexes, and relationships.

---

## 5. Security Architecture Reference

> [!IMPORTANT]
> The official long-term security architecture lives in:
> [`docs/architecture/security-architecture-v1.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
>
> That document is the **future target and migration guideline**. The current repository is transitional. **Code + tests remain the source of truth for active behavior.** Do not force domain refactors solely because they do not yet match the target.

### Summary (do not duplicate the full reference here)

| Concern | Target direction |
| :--- | :--- |
| Identity | `User` (auth) · `ClubMembership` (ops) · `PlayerProfile` → `ClubPlayer` (customers, future) |
| Authentication | Answers “who?” only (JWT, future password change). Never club/court/resource access. |
| Authorization | Spine owns WHERE/WHO · domain `authorization.py` owns special business rules only |
| Resource config | Implemented `authorization_config` contract only — no second metadata format |
| ViewSets | Compose optional `SlotyScopedResourceMixin` with DRF; do not replace `ModelViewSet` |
| Migration | Courts ✅ · Transactions ✅ · Settlements ✅ · Identity Foundation ✅ → Bookings → Dashboard → Reports → Audit → remove legacy |

### Transitional runtime notes

- Spine code: [`apps/common/authorization/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/)
- Legacy access (`ClubAccessContext` / `ClubScopedAccessMixin`) still serves unmigrated domains.
- `ClubMembership.last_sync_at` is updated **only** by `POST /api/v1/me/sync-heartbeat/` — never by spine mixins.

---

## 6. Global API Conventions

- **Versioning**: All public endpoints are versioned under `/api/v1/`. Club-scoped APIs live under `/api/v1/clubs/{club_slug}/...`.
- **Tenant Isolation**: Never trust client-supplied club IDs in request bodies or query params. Scoping comes from the authenticated user's verified club access.
- **Standardized Error Responses**:
  - Business errors and rule violations raise [`SlotyAPIException`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/exceptions.py) with a stable `code` and localized `message`.
  - Normalization is handled globally by `sloty_exception_handler`. Never return ad-hoc error dictionaries like `{"booking": "error"}`.
- **Query Parameter Filtering**:
  - List filters must be declared in domain `filters.py` via `django-filter` `FilterSet` classes.
  - `FilterSet` classes must not perform permission checks or query memberships.
  - `ViewSet.get_queryset()` must return an already authorized and scoped queryset before filters are applied.
- **Schema & Documentation**: OpenAPI schema is exposed at `/api/v1/schema/` and Swagger UI at `/api/v1/docs/`.

---

## 7. Database & Concurrency Rules

- **Transactions**: Multi-model writes and state transitions must be wrapped in `transaction.atomic()`.
- **Concurrency & Locking**: When concurrent requests can conflict (e.g. booking creation, slot reservation, rescheduling, settlements, transaction cancellation), lock affected rows using `select_for_update()`.
- **Migrations**: Every model change requires a migration. Avoid data migrations inside schema migration files.
- **Timezone**: Django `TIME_ZONE = "Africa/Cairo"` with `USE_TZ = True`. Datetimes are stored as UTC in the database and translated to local Cairo time for business logic.

---

## 8. Testing Rules

- **Framework**: `pytest` configured via `pyproject.toml` using `config.settings.test`.
- **Test Locations**:
  - Domain tests live under `tests/<domain>/` (e.g., `tests/bookings/`, `tests/settlements/`).
  - Cross-cutting tests live under `tests/`.
  - Note: Report tests live under `apps/reports/tests/`.
- **Test Strategy**:
  - Test services for business logic, status transitions, and domain invariants.
  - Test viewsets for authorization, scoping, validation errors, and response contracts.
  - Run targeted tests before committing changes.

### Default test execution

Prefer parallel execution with database reuse for normal verification:

```bash
pytest -n 4 --reuse-db
```

Do **not** default to sequential `pytest`. This repository supports parallel workers (`pytest-xdist`) and `--reuse-db` is the validated database strategy. Together they significantly reduce verification time.

Domain examples:

```bash
pytest -n 4 --reuse-db tests/players/
pytest -n 4 --reuse-db tests/bookings/
pytest -n 4 --reuse-db tests/transactions/
pytest -n 4 --reuse-db tests/settlements/
```

Combined regression:

```bash
pytest -n 4 --reuse-db tests/players/ tests/bookings/ tests/transactions/ tests/settlements/
```

Before a large suite, confirm `pytest-xdist` is available (`pytest -n 4 --collect-only` or `python -c "import xdist"`) and that `--reuse-db` is accepted (`pytest --help` lists it via `pytest-django`). Use the optimized command by default. Fall back to sequential `pytest` only when a specific test cannot safely run in parallel.

### Failure classification (parallel runs)

When parallel tests fail, do **not** immediately assume a code regression. Classify first:

1. Real application regression.
2. Existing flaky test.
3. Database concurrency / infrastructure issue.
4. Test isolation problem exposed by parallel execution.

Investigate before changing application code.

### Database handling

Do not unnecessarily recreate the test database. Prefer `--reuse-db` unless:

- schema changes require recreation (`--create-db` after migrations),
- database corruption is confirmed,
- migrations themselves are being tested.

---

---

## 9. Code Quality & Tooling

- **Formatting & Linting**:
  - Black and isort configurations live in `pyproject.toml`.
  - Flake8 configuration lives in `setup.cfg`.
  - Pre-commit configuration lives in root `.pre-commit-config.yaml`.
- **Change Discipline**:
  - Keep changes focused strictly on the requested task.
  - Do not refactor unrelated code.
  - If a code bug or discrepancy is discovered during an architecture or documentation task, document it as a finding rather than silently fixing it.

---

## 10. Operational Management Commands

Key Django management commands for maintenance and development:

- **Expire Hold Bookings**:
  ```bash
  python manage.py expire_hold_bookings
  ```
  Evaluates unconfirmed `HOLD` bookings whose start time has arrived or whose court internal hold expiry duration has elapsed, transitioning them to `EXPIRED` and releasing their time slots.
- **Seed Demo Data**:
  ```bash
  python manage.py seed_demo_data
  # Or with specific test scenario:
  python manage.py seed_demo_data --scenario albaladya-test
  ```
  Idempotently populates multi-club test fixtures, demo users (`owner`, `manager`, `staff`), courts, working hours, bookings, transactions, and settlements for local development and manual testing.
