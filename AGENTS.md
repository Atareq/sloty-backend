# AGENTS.md — Global Guide & Routing

## 1. Purpose & Project Identity

This file is the root engineering guide and documentation router for AI coding agents and developers working in `sloty-backend`.

**Sloty** is a sports court rental management system (starting with football courts in Assiut and Upper Egypt). The backend is built with Django and Django REST Framework (DRF) as a modular monolith.

### Core Rule: SIMPLICITY WINS

Keep the implementation direct, minimal, and clean.
- Do not introduce abstractions unless they solve a demonstrated, active problem.
- Do not store synthetic lifecycle flags or version chains when domain entities (e.g. Booking relationships, immutable transaction records) already serve as the single source of truth.
- Do not preserve complexity merely because a previous implementation had it.

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
| **Profiles** | [`apps/profiles/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/profiles/AGENTS.md) | Application role authority: `Profile`, `OwnerProfile`, `StaffProfile`, `AdminProfile`, and `PlayerProfile`. |
| **Clubs** | [`apps/clubs/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/AGENTS.md) | Club tenant definitions, location validation, and profile-backed club scope. |
| **Courts** | [`apps/courts/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/AGENTS.md) | Court settings, working hours, pricing periods, midnight/slot time semantics. |
| **Bookings** | [`apps/bookings/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/AGENTS.md) | Booking lifecycle, agreed price snapshots, overlap protection, recurrence, hold expiry (see [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)). |
| **Transactions** | [`apps/transactions/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/AGENTS.md) | Immutable financial ledger, payment thresholds, cancellation/correction workflow. |
| **Settlements** | [`apps/settlements/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/AGENTS.md) | Cash closing, Current Custody (Club + Collector), preview/settle workflows. |
| **Audit** | [`apps/audit/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/AGENTS.md) | Append-only activity trail, stable machine actions, event snapshot architecture. |
| **Dashboard** | [`apps/dashboard/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/AGENTS.md) | Operational vs. financial metrics, calendar, public availability contract. |
| **Reports** | [`apps/reports/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/AGENTS.md) | Analytical reports, court usage metrics, 31-day window, occupancy clipping. |
| **Common** | [`apps/common/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/AGENTS.md) | Egypt locations, standardized error handling (`SlotyAPIException`), Authorization Spine, search, middleware, global timestamp bases. |
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
| Identity | `User` (auth) · `Profile` (application role; inherited `Profile.user` optional for `PlayerProfile`) · `PlayerProfile` → `ClubPlayer` → `Booking.club_player` (customers) |
| Authentication | Answers “who?” only (`phone_number` + password, JWT, and own-password change). Never club/court/resource access. There is no username. |
| Authorization | Spine owns WHERE/WHO · domain `authorization.py` owns special business rules only |
| Resource config | Implemented `authorization_config` contract only — no second metadata format |
| Migration | Courts ✅ · Transactions (auth Spine v2) ✅ · Settlements (auth Spine v2) ✅ · Identity Foundation ✅ · Bookings (auth Spine v2) ✅ · Dashboard (auth Spine) ✅ · Audit (auth Spine v2) ✅ · Reports (auth Spine) ✅ · Clubs (auth Spine v2) ✅ · Auth & Lifecycle Hardening (Sprint 15/16) ✅ · Authorization Matrix & Compatibility Cleanup (Sprint 17) ✅ |
| Migration | Courts ✅ · Transactions (auth Spine v2) ✅ · Settlements (auth Spine v2) ✅ · Identity Foundation ✅ · Bookings (auth Spine v2) ✅ · Dashboard (auth Spine) ✅ · Audit (auth Spine v2) ✅ · Reports (auth Spine) ✅ · Clubs (auth Spine v2) ✅ · Auth & Lifecycle Hardening (Sprint 15/16) ✅ · Authorization Matrix & Compatibility Cleanup (Sprint 17) ✅ · Booking Identity Finalization (Sprint 18) ✅ · Cross-Domain Consistency (Sprint 19) ✅ |

### Current runtime notes

- Spine code: [`apps/common/authorization/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/)
- Club scope is derived from the current `Profile`: an owner through `OwnerProfile.clubs`, staff through `StaffProfile.club` and `StaffCourtAccess`, and an admin by `Profile.role == ADMIN`. `User.profile` is a nullable one-to-one used only when a profile is linked to an authenticated account. `Profile` is the concrete Django multi-table parent; Owner/Staff/Admin/Player profiles inherit its identity, fields, and common `clean()`/`save()`. Owner/Staff/Admin require `user`; Player may be unlinked until future signup/verification. Children hold only role-specific data. Disabled profiles grant no resource access.
- There is no heartbeat/presence API. Authorization mixins, ordinary API traffic, login, refresh, `/me`, and scope resolution never write presence state.
- **Offline & Reconnect Safety**: Server is authoritative. Reconnect mutations require idempotency keys (`client_request_id`). Duplicate requests return `200 OK` with the existing entity; mismatched payloads return `409 Conflict`. Financial mutations (payments, refunds, settlements) require live server authority and can never be committed offline.
- **Account and Profile Lifecycle**: `User` is authentication identity; the current `Profile` is application-role authority. A missing or invalid profile scope is denied immediately (`CLUB_ACCESS_REVOKED`), regardless of JWT claims.
- **Business Actor IDs**: Actor and custody IDs accepted or returned by business APIs are `Profile.id` values. Display names may come from `Profile.user`; authentication account IDs are not accepted as alternate actor IDs.
- **Transaction Settlement State**: Transaction responses expose read-only `is_settled`, derived from the existing one-to-one `settlement_line`. Never add or synchronize a duplicate settlement-status flag on Transaction.
- **Transaction Cancellation Window**: Settled transactions are never cancellable. Unsettled transactions have a 15-minute direct-correction safety window; after it, direct cancellation is allowed only while the linked booking remains inside the court's full-refund cancellation window. Late corrections return `PAYMENT_CANCELLATION_REQUIRES_BOOKING_CANCELLATION` and must use booking cancellation so `cancellation_refund_notice_days` retention/refund policy is preserved.
- **Current Custody**: Settlement preview, grouped unsettled summary, and settlement creation consume the same all-time club-wide calculation. Optional collector filtering uses `Transaction.created_by`; Court is metadata and never narrows settlement candidates. Creation recalculates while locking candidates inside `transaction.atomic()`.
- **Demo Seed Identity**: `seed_demo_data` creates only the local `testing` club, keeps `User` rows for authentication, and creates explicit `AdminProfile`, `OwnerProfile`, and `StaffProfile` actors. The testing club's business rows store those Profile objects directly; rerunning the seed remains idempotent.
- **Scheduling contract**: `apps/bookings` owns generated slot availability and stored HOLD deadlines. Read [`docs/scheduling-contract.md`](docs/scheduling-contract.md) and the live OpenAPI schema at `/api/v1/schema/` before changing booking or frontend-facing scheduling behavior.
  Scheduling uses canonical half-open `[start_time, end_time)` intervals across pricing, slots, and booking conflicts; cross-midnight periods are resolved as timezone-aware local datetimes.
  The slots endpoint returns one unified `slots` timeline: generated slots are
  merged with visible booking records, and bookings outside configured working
  hours are returned as `slot_type=BOOKING_ONLY` items. The top-level response
  contains only `slots`. Unoccupied, priced slots in the previous 48 hours are
  available for historical booking; POST still revalidates server-side rules.

---

## 6. Global API Conventions

- **Versioning**: All public endpoints are versioned under `/api/v1/`. Club-scoped APIs live under `/api/v1/clubs/{club_slug}/...`.
- **Tenant Isolation**: Never trust client-supplied club IDs in request bodies or query params. Scoping comes from the authenticated user's verified club access.
- **Standardized Error Responses**:
  - Business errors and rule violations raise [`SlotyAPIException`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/exceptions.py) with a stable `code` and localized `message`.
  - Normalization is handled globally by `sloty_exception_handler`. Never return ad-hoc error dictionaries like `{"booking": "error"}`.
- **Query Parameter Filtering**:
  - List filters must be declared in domain `filters.py` via `django-filter` `FilterSet` classes.
  - `FilterSet` classes must not perform permission checks or query club scope.
  - `ViewSet.get_queryset()` must return an already authorized and scoped queryset before filters are applied.
- **Schema & Documentation**: OpenAPI schema is exposed at `/api/v1/schema/` and Swagger UI at `/api/v1/docs/`.

---

## Phone Number Handling Contract

Phone numbers are identity-related data and must follow one validation path.

Whenever an API request or response contains a phone number:

- MUST use:

from phonenumber_field.serializerfields import PhoneNumberField

- MUST NOT use serializers.CharField for phone fields.
- MUST NOT implement custom regex validation.
- MUST NOT manually parse country codes inside serializers.

Normalization responsibility:

- API boundary: PhoneNumberField
- Database: canonical E.164 representation
- Services: consume validated phone values

Any new endpoint exposing phone numbers must follow this rule.

---

## 7. Database & Concurrency Rules

- **Transactions**: Multi-model writes and state transitions must be wrapped in `transaction.atomic()`.
- **Concurrency & Locking**: When concurrent requests can conflict (e.g. booking creation, slot reservation, rescheduling, settlements, transaction cancellation), lock affected rows using `select_for_update()`.
- **Migrations**: Every model change requires a migration. Avoid data migrations inside schema migration files. The September 2026 pre-release Profile refactor rebuilt first-party migrations from the final models. Each model-bearing app starts at `0001_initial.py`; Django may also emit `0002_initial.py` where circular foreign keys require a second file. Recreate development/test databases when adopting this baseline; do not migrate an obsolete database forward or fake its history. Tests use the real migration graph. After a reset, create the template sequentially (`pytest --create-db` on a small file or without `-n`), then run `pytest -n 4 --reuse-db`. Do not combine `-n` with `--create-db` on PostgreSQL: workers race `DROP`/`CREATE DATABASE` and fail with `DuplicateDatabase`, `ObjectInUse`, or `SSL connection has been closed unexpectedly`.
- **Timezone**: Django `TIME_ZONE = "Africa/Cairo"` with `USE_TZ = True`. Datetimes are stored as UTC in the database and translated to local Cairo time for business logic.

### Global Timestamp Contract

All models that need timestamps MUST inherit from
[`apps.common.models.SlotyCreatedModel`](apps/common/models.py) or
[`apps.common.models.SlotyTimeStampedModel`](apps/common/models.py).

- Never declare `created` / `modified` fields manually.
- Never introduce `created_at` / `updated_at` without architecture approval.
- Immutable / append-only records (`Transaction`, `TransactionAttempt`,
  `BookingAttempt`, `AuditLog`, `Settlement`, `SettlementTransaction`,
  `StaffCourtAccess`) use `SlotyCreatedModel` (`created` only).
- Mutable business entities (`Profile` and children, `Club`, `Court`,
  `Booking`, and similar operational rows) use `SlotyTimeStampedModel`
  (`created` + `modified` from django-extensions `TimeStampedModel`).
- Do not inherit `TimeStampedModel` directly. Models that do not need
  timestamps stay on `models.Model` (or `AbstractUser`).

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

### Default test execution (required for AI coding agents)

**Always** run tests with parallel workers and database reuse. This is the project-standard command, not an optimization to probe first:

```bash
pytest -n 4 --reuse-db
```

Do **not** default to sequential `pytest`. Do **not** spend a step checking whether `pytest-xdist` exists before using `-n 4`. The combination is validated and safe for this repository.

Scoped examples:

```bash
pytest -n 4 --reuse-db tests/players/
pytest -n 4 --reuse-db tests/bookings/
pytest -n 4 --reuse-db tests/transactions/
pytest -n 4 --reuse-db tests/settlements/
pytest -n 4 --reuse-db tests/authorization/
```

Combined regression:

```bash
pytest -n 4 --reuse-db tests/players/ tests/bookings/ tests/transactions/ tests/settlements/
```

If a parallel run reports failures, **retry only the failed node** sequentially to confirm it is a real regression (not a worker/collection artifact):

```bash
pytest --reuse-db path/to/test_file.py::TestClass::test_name
```

Fall back to a full sequential `pytest --reuse-db …` suite only after isolated retries, or when a specific test is proven unsafe under xdist.

### Failure classification (parallel runs)

When parallel tests fail, do **not** immediately assume a code regression. Classify first:

1. Real application regression.
2. Existing flaky test.
3. Database concurrency / infrastructure issue.
4. Test isolation problem exposed by parallel execution.

Investigate before changing application code.

### Database handling

Do not unnecessarily recreate the test database. Prefer `--reuse-db` unless:

- schema changes require recreation (`pytest --create-db` sequentially after migrations, then `pytest -n 4 --reuse-db`),
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
  Each HOLD stores `hold_expires_at` when it is created, using the court's
  configured duration at that time. Active occupancy requires
  `status=HOLD AND hold_expires_at > now`; changing court settings later does
  not change an existing deadline. This command expires rows with
  `status=HOLD AND hold_expires_at <= now`. Production uses
  [`scripts/systemd/sloty-hold-expiry.timer`](scripts/systemd/sloty-hold-expiry.timer)
  every 5 minutes. The timer is cleanup only; availability does not depend on it.
- **Seed Demo Data**:
  ```bash
  python manage.py seed_demo_data
  ```
  Idempotently populates one `testing` club, demo users (`platform_admin`,
  `owner`, `staff`), profile extensions, courts, working hours, bookings,
  related transactions, settlements, and audit logs for local development and
  manual testing.

- **Runtime version**:
  ```text
  GET /api/system/version/
  ```
  Unauthenticated (`AllowAny`). Reads `/etc/sloty/backend-release.json` (no database). `commit` is the deployed Git SHA; `release` is the human-readable tag. Missing metadata returns null fields. See [`apps/common/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/AGENTS.md).
- **Production deploy isolation**: Canonical executable is [`scripts/deploy-sloty-backend`](file:///home/tarek/Desktop/sloty/sloty-backend/scripts/deploy-sloty-backend), and its systemd unit is [`deploy/systemd/sloty-backend.service`](file:///home/tarek/Desktop/sloty/sloty-backend/deploy/systemd/sloty-backend.service). Production CI runs `sudo /srv/sloty/backend/scripts/deploy-sloty-backend <SHA> <TAG>`. The script discovers the git root from its location, snapshots `scripts/` to `/tmp` before checkout, uses `<repo>/.venv/bin/python` (must be Python 3.12.14 from `/opt/sloty-runtime/python/`), loads `/etc/sloty/env/prod.env`, runs Django check/migrate/collectstatic, restarts only `sloty-backend.service`, and requires HTTP 200 from `http://127.0.0.1:8020/health/` with `Host: api.sloty.baronlearning.com`. The backend service must not load `/etc/sloty/env/backup.env`. It must not manage system Python, other project virtualenvs, Nginx, or the shared PostgreSQL service.
- **Database backups**: Repo-owned entrypoints are [`scripts/backup-sloty-db`](file:///home/tarek/Desktop/sloty/sloty-backend/scripts/backup-sloty-db), [`scripts/restore-sloty-db`](file:///home/tarek/Desktop/sloty/sloty-backend/scripts/restore-sloty-db), and [`scripts/install-host-dependencies`](file:///home/tarek/Desktop/sloty/sloty-backend/scripts/install-host-dependencies). Timer/service units live in `scripts/systemd/`; the root backup service alone loads both env files and writes root-only archives under `/var/backups/sloty/postgres`. Restore requires an explicit database, and `sloty_prod` additionally requires exact production confirmation. Never run these scripts against another project's database or backup paths.
