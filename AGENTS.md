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
        ↓
Scoped Domain AGENTS.md (under apps/<domain>/)
        ↓
Root AGENTS.md
```

**Rule**: Code and tests are authoritative. If any `AGENTS.md` file conflicts with actual implementation, **the code wins**. Update the documentation to reflect reality rather than introducing breaking code changes or assumptions.

---

## 3. Documentation Routing

Domain-specific business rules, models, invariants, and workflows live inside scoped domain guides. **Read the corresponding scoped guide before modifying code in any domain app:**

| Domain / App | Path | Primary Ownership |
| :--- | :--- | :--- |
| **Accounts** | [`apps/accounts/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/AGENTS.md) | User identity, platform super admin authority, auth endpoints, orphan diagnostics. |
| **Clubs** | [`apps/clubs/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/AGENTS.md) | Club tenant definitions, `ClubMembership` lifecycle, location validation, access engine. |
| **Courts** | [`apps/courts/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/AGENTS.md) | Court settings, working hours, pricing periods, midnight/slot time semantics. |
| **Bookings** | [`apps/bookings/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/AGENTS.md) | Booking lifecycle, agreed price snapshots, overlap protection, recurrence, hold expiry. |
| **Transactions** | [`apps/transactions/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/AGENTS.md) | Immutable financial ledger, payment thresholds, cancellation/correction workflow. |
| **Settlements** | [`apps/settlements/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/AGENTS.md) | Cash closing, Current Custody (Club + Collector), preview/settle workflows. |
| **Audit** | [`apps/audit/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/AGENTS.md) | Append-only activity trail, stable machine actions, event snapshot architecture. |
| **Dashboard** | [`apps/dashboard/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/AGENTS.md) | Operational vs. financial metrics, calendar, public availability contract. |
| **Reports** | [`apps/reports/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/AGENTS.md) | Analytical reports, court usage metrics, 31-day window, occupancy clipping. |
| **Common** | [`apps/common/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/AGENTS.md) | Egypt locations, standardized error handling (`SlotyAPIException`), search, middleware. |

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

- **URLs**: Route requests and extract URL parameters (e.g. `club_slug`).
- **Views**: Handle HTTP status, permissions, serializer selection, and queryset scoping.
- **Serializers**: Validate request payload shape and format response representation. The code, serializers, and OpenAPI schemas (`/api/v1/schema/`) are the authoritative contract for request/response shapes.
- **Services**: Own transactions, domain state machines, concurrency locks, multi-model writes, and audit recording.
- **Models**: Define database constraints, schema invariants, indexes, and relationships.

---

## 5. Authorization Spine Architecture

The authorization spine separates facts, permissions, query scoping, and business rules:

```text
Authentication
      ↓
request.user / profile
Scope Resolution (resolve_club_scope)
      ↓
URL club scope
RequestAccessContext (Facts: user, role, club, court, admin flag)
      ↓
SlotyBasePermission (Role + ViewSet + DRF Action → allow / deny)
      ↓
QuerySet Scoping (data filtering)
      ↓
Object Permission (extension hook for domain object rules)
      ↓
Serializer Validation
      ↓
Domain Service (state machines, concurrency locks, audit logs)
```
### Architectural Principles
- **Context = Facts**: [`RequestAccessContext`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/context.py) provides factual data only. It contains **no** endpoint authorization decisions (`can_create_booking()`, etc.) and **no** queryset logic.
- **Scope Resolution**: [`resolve_club_scope()`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/resolver.py) enforces URL club authority for `/api/v1/clubs/{club_slug}/...`. Missing or revoked access raises `CLUB_ACCESS_REVOKED` (403). Context is cached on `request._access_context`.
- **Role Matrix**: Centralized in [`ROLE_PERMISSIONS`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/matrix.py) with four operational roles (`ADMIN`, `OWNER`, `MANAGER`, `STAFF`). Strict default deny. No capability abstraction.
- **Base Permission**: [`SlotyBasePermission`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/permissions.py) evaluates `Role + ViewSet + DRF Action`. Exposes `has_object_permission` as a clean extension hook.

> [!NOTE]
> **Transitional State**:
> The new authorization spine foundation is implemented under [`apps/common/authorization/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/). Existing domains still operate on the legacy access layer (`ClubAccessContext`, `ClubScopedAccessMixin`) until migrated domain-by-domain in subsequent tasks.

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
  - Run targeted tests before committing changes (e.g., `pytest tests/bookings`).

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
