# Sloty Security Architecture v1

**Status:** Official long-term security architecture reference
**Audience:** Backend engineers and AI coding agents
**Last updated:** 2026-09-13

---

## Important Warning

> [!WARNING]
> **This document is the future security architecture reference.**
>
> If any section does not match the current repository implementation, **do not force implementation changes**. The current codebase, tests, and approved migration tasks remain the source of truth for active behavior.
>
> Future work should move the system **gradually** toward this architecture while preserving existing behavior.
>
> Do **not** treat mismatches between this document and the repository as bugs.
> Do **not** refactor domains only because they do not yet match this target.

### How to read this document

Every major section separates three layers:

| Layer | Meaning |
| :--- | :--- |
| **Current State** | What exists in the repository today (transitional). |
| **Target State** | Desired final architecture. |
| **Migration Notes** | Why the change exists, what it replaces, and what must stay stable during migration. |

**Authority order for active behavior:**

```text
Code + Database Models
        ↓
Tests
        ↓
Approved Product/API Contracts
        ↓
This reference (direction) + scoped AGENTS.md (domain rules)
```

---

## 0. Implementation Snapshot (Transitional)

### Implemented today

- `User` authentication identity (`apps/accounts`)
- `ClubMembership` club operational relationship (`apps/clubs`)
- JWT access + refresh authentication
- Authorization Spine foundation (`apps/common/authorization/`)
  - `RequestAccessContext`
  - `resolve_club_scope` / `resolve_global_scope`
  - `SlotyBasePermission` + role matrix
  - `ClubScopedViewMixin`
  - v2 resource query: `authorization_config`, `scoped_queryset`, `SlotyScopedResourceMixin`
- Explicit sync heartbeat (`POST /api/v1/me/sync-heartbeat/`) — not part of authorization
- Domain migrations:
  - **Courts** ✅ — Spine v2 (`authorization_config` + `SlotyScopedResourceMixin`)
  - **Transactions** ✅ — Spine v2 (`authorization_config` + `SlotyScopedResourceMixin`) plus domain collector/cancel/dismiss rules (`apps/transactions/authorization.py`)
  - **Settlements** ✅ — Spine v2 (`authorization_config` club-only + `SlotyScopedResourceMixin`) plus collector/settle-authority rules (`apps/settlements/authorization.py`)
  - **Identity Foundation** ✅ — `PlayerProfile` + `ClubPlayer` (`apps/players/`); `ClubPlayer` on Spine v2
  - **Booking Identity Linkage (Phase A)** ✅ — `Booking.club_player` FK (nullable) added; `create_booking()` auto-resolves identity. **Not** a direct `player_profile` FK — see §1 migration notes.
  - **ClubPlayer Historical Identity Foundation (Sprint 1)** ✅ — later superseded by Sprint 3 versioning (no soft delete).
  - **Sprint 3 — Player Identity & Booking Integration** ✅ — `ClubPlayer` versions (`previous_version`, `is_current_version`); no `last_used_at` / no `updated_at`; last-used version is the latest booking's `club_player` for that `player_profile_id`; booking create uses current version; optional historical `club_player_id`; player search returns versions + recommended id.
  - **Sprint 4 — Consumer Migration To ClubPlayer Identity** ✅ — operational reads (booking list/detail/slots, dashboard calendar, transaction/settlement customer display, search) use `Booking.club_player` with snapshot fallback. API keys unchanged. Snapshot columns not removed. Audit event JSON and BookingAttempt payloads still store snapshots.
  - **Bookings authorization (Phase B)** ✅ — Spine v2 (`authorization_config` + `SlotyScopedResourceMixin` + `SlotyBasePermission`). Boundary is Club + Court, not creator. BookingAttempt keeps Staff `attempted_by` restriction.
  - **Dashboard** ✅ — Spine (`ClubScopedViewMixin` + `SlotyBasePermission` + source-domain `scoped_queryset`); public availability remains anonymous
  - **Audit** ✅ — Spine v2 (`authorization_config` club-only + `SlotyScopedResourceMixin` + `SlotyBasePermission`). Staff matrix-denied. Not court- or collector-scoped.
  - **Reports** ✅ — Spine read-model (`ClubScopedViewMixin` + `SlotyBasePermission` + source-domain `scoped_queryset`). Court Usage is Club + Court; Staff matrix-denied.

### Not yet migrated / not yet created

- Parts of Clubs still on legacy access layer
- Full `authorization_config` adoption across all secured models
- Removal of legacy `ClubAccessContext` / `ClubScopedAccessMixin`
- Password change endpoint (planned)
- Password reset (future; not scheduled)

---

## 1. Identity Architecture

### Target State

```text
Authentication Identity
        │
       User
        │
        ├── Club Operational Actor ──► ClubMembership
        │
        └── Customer Identity ──► PlayerProfile ──► ClubPlayer
```

| Concept | Model | Responsibility |
| :--- | :--- | :--- |
| Authentication identity | `User` | Login credentials, password hash, activity/security state, platform admin flag |
| Club operational actor | `ClubMembership` | Club relationship, role (`OWNER` / `MANAGER` / `STAFF`), court assignment, manager delegation flags |
| Global customer person | `PlayerProfile` | Unique phone identity, optional name, verified flag, nullable `user` FK |
| Club-local customer label | `ClubPlayer` | Per-club display name, club number, metadata; immutable versioned historical record — at most one **current** row per `(club, player_profile)` (`UNIQUE(club, player_profile) WHERE is_current_version=True`); identity changes create a new version (`previous_version`, `is_current_version`), never edit or soft-delete |

**Clarify:**

- `User` = authentication identity (“who can log in?”)
- `ClubMembership` = club operational relationship (“what role does this user have in this club?”)
- `PlayerProfile` = global customer identity (“which person is this phone?”)
- `ClubPlayer` = club-specific representation (“how does this club name/number that person?”)

**Do not introduce** `StaffProfile` / `OwnerProfile` / `ManagerProfile` unless a future product requirement proves `ClubMembership` insufficient. Today it already carries role, court, and flags.

### Current State

- `User` exists and is identity-only (no club roles stored on the user row).
- `ClubMembership` exists and is the operational actor.
- `PlayerProfile` and `ClubPlayer` exist (`apps/players/`). `ClubPlayer` is an immutable versioned historical identity record (`previous_version`, `is_current_version`, `created_at`; no `deleted_at`, no `last_used_at`, no `updated_at`) — last-used version is derived from the latest `Booking` for that `player_profile_id` at the club — see `apps/players/AGENTS.md` "ClubPlayer Lifecycle (Versioned, Append-Only)".
- Bookings store denormalized `customer_name` / `customer_phone` (permanent snapshots — kept intentionally, not migration debt) **and** a resolved `club_player` FK (`Booking.club_player`, nullable, populated by `create_booking()`). `Booking` does **not** have a direct `player_profile` FK — see Migration Notes below. Because `ClubPlayer` is itself historical, `Booking.club_player_id` is unaffected by any later supersede of that row.
- `RequestAccessContext.profile` / `profile_type` are forward-compat aliases of membership/role — not separate profile tables.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Separate login identity from club ops and from customers who may never create an account. |
| **Replaces** | Free-text booking customer fields as the long-term person model; avoids stuffing club roles onto `User`. |
| **Must remain unchanged** | Existing login, membership onboarding, and booking customer snapshot fields (`customer_name`/`customer_phone` stay forever). |

> [!NOTE]
> **Booking identity refinement (locked):** [`ADR-002`](adr/ADR-002-booking-identity-and-authorization-migration.md) Decision 1 originally proposed *two* nullable FKs on `Booking` (`player_profile` and `club_player`). The implemented Phase A design uses **only `club_player`** — `Booking` belongs to a `ClubPlayer`; `PlayerProfile` is reachable transitively via `club_player.player_profile`. `ClubPlayer.player_profile` is immutable after creation, so a second direct FK on `Booking` would add redundancy without protecting a distinct invariant. See the ADR-002 addendum.

---

## 2. Authentication Architecture

### Target State

Authentication answers only:

> **Who is this user?**

**Owns:**

- login
- JWT access token issue / validation / expiry
- refresh tokens
- password change (own password only)
- future password reset (email/SMS/OTP — separate phase)

**Must not decide:**

- club access
- court access
- booking visibility
- financial / custody scope

Those belong to authorization.

```text
Authentication
        │
Authenticated User
        │
Authorization Context
        │
Resource Access
```

**JWT rule:** Optional claims (`role`, `club_id`, `court_id`, …) are **UX convenience only**. They are never authority. Authority comes from:

```text
Request URL → Scope Resolver → Access Context → Authorization Spine
```

**Password change (planned):** authenticated user only; verify current password; update only `request.user`. No admin-changing-another-user through this endpoint.

### Current State

- JWT obtain + refresh endpoints exist.
- Optional `club_slug` on token obtain may embed convenience claims.
- Password change / reset endpoints are not implemented yet.
- Sync heartbeat is authentication-adjacent presence bookkeeping, **not** authorization.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Keep “who” separate from “what may they access” so tokens cannot become a second permission system. |
| **Replaces** | Any client habit of treating JWT club/role claims as access proof. |
| **Must remain unchanged** | Existing token obtain/refresh contracts until an explicit auth task changes them. |

---

## 3. Authorization Spine

### Target State

```text
Request
  ↓
Authentication
  ↓
AccessContext (facts only)
  ↓
Resource Resolver
  ↓
Permission Matrix (Role × ViewSet × Action)
  ↓
Scoped QuerySet
  ↓
Optional domain business-rule checks
  ↓
Business Logic (services)
```

**Authorization owns:**

- identity facts (authenticated user, role, membership, platform admin)
- role evaluation
- club scope
- resource scope (court, future keys)
- query boundary (fail-closed secured queryset)

**Services own:**

- business rules
- calculations
- state transitions
- multi-model writes / audit recording

Services should **not** normally contain `if user.role == ...` or re-check club/court access.

**Context = facts only.** Access context must not embed endpoint permission helpers or queryset builders.

### Current State

- Spine v1 + v2 foundation live under `apps/common/authorization/`.
- Courts use v2 resource query mixin.
- Bookings use v2 resource query mixin (club + court; not creator-scoped).
- Transactions use v2 resource query mixin (club + court) plus Staff collector `created_by` narrowing.
- Settlements use v2 resource query mixin (club only, never court) plus collector narrowing in the domain module.
- Dashboard uses Spine context + matrix (`DashboardViewSet` actions) and source-domain `scoped_queryset` aggregations.
- Audit uses v2 resource query mixin (club only, never court). Staff are matrix-denied.
- Reports uses Spine context + matrix (`CourtUsageReportViewSet` list) and source-domain `scoped_queryset` aggregations.
- Parts of Clubs still use legacy `ClubAccessContext`.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | One security spine prevents each domain inventing its own access engine. |
| **Replaces** | Legacy `ClubAccessContext` permission helpers + per-domain scoped queryset methods, domain by domain. |
| **Must remain unchanged** | Migrated domain behavior (Courts / Transactions / Settlements / Bookings) and unmigrated legacy behavior until each domain’s migration task. |

---

## 4. Resource Scope Architecture

### Target State

Built-in scopes:

| Scope | Meaning |
| :--- | :--- |
| `NONE` | Explicit fail-closed; no generic resource access |
| `CLUB` | Validated URL club boundary |
| `COURT` | Club first, then court (explicit URL court or staff assignment) |

Future scopes may be added **only when justified**, with both:

1. a model ORM path, and
2. a matching fact (or `<scope>_ids`) on the access context.

Missing facts → empty queryset (fail closed).

### Model contract (implemented shape is authoritative)

Every authorization-aware model may declare:

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
        "court": {"path": "court"},  # or "self" when the model is the boundary
    },
    "default_scope": "court",  # or "club" / "none"
    "select_related": (),
    "prefetch_related": (),
}
```

**Rules:**

- The **current implemented contract** in `apps/common/authorization/contracts.py` is the authority.
- Do **not** create another metadata format.
- Club isolation is always applied first for scoped resources.
- `"self"` is reserved when the resource row *is* the scope boundary (e.g. `Court`).
- Missing/malformed config must raise — never fall back to `Model.objects.all()`.

### Current State

- Contract implemented and tested.
- Courts (`Court`, `CourtWorkingHour`) declare `authorization_config`.
- Bookings (`Booking`, `BookingAttempt`) declare `authorization_config`.
- Transactions (`Transaction`, `TransactionAttempt`) declare `authorization_config`.
- Settlements (`Settlement`) declare `authorization_config` with club-only scope (`default_scope="club"`). Collector narrowing remains domain business logic.
- Audit (`AuditLog`) declares `authorization_config` with club-only scope (`default_scope="club"`). Court is an optional event attribute, not a security boundary. Staff are matrix-denied.

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
    },
    "default_scope": "club",
    "select_related": (
        "club",
        "court",
        "collected_by",
        "created_by",
        "settled_by",
    ),
    "prefetch_related": (),
}
# Collector filtering = domain business narrowing — never Court.
```

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Make security boundaries explicit on models and avoid N+1 / ad-hoc scoping. |
| **Replaces** | Hand-built scoped querysets inside legacy access context methods. |
| **Must remain unchanged** | The `authorization_config` key names and semantics already shipping in common authorization. |

---

## 5. Scoped Query Pattern

### Target State

Every secured endpoint follows:

```text
URL Resource
  ↓
Resolve scope
  ↓
Authorize (matrix / object hooks)
  ↓
Build secured QuerySet
  ↓
Apply business filters (narrow only)
  ↓
Pagination
  ↓
Serializer
```

**Never:**

```text
Fetch everything → filter in Python
```

**Never** rely on frontend filtering for security.

Tenant IDs in request bodies/query params are not authoritative; URL + resolved membership are.

### Current State

- Spine-migrated ViewSets follow this pattern (Courts, Bookings, Transactions, and Settlements via mixin). Settlements are club-scoped; collector narrowing happens after the Spine queryset.
- Dashboard authenticated endpoints are GenericAPIViews on `ClubScopedViewMixin` + `SlotyBasePermission`; they aggregate already-scoped source querysets.
- Legacy domains still centralize scoping inside `ClubAccessContext` helpers.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Security must happen before filters/pagination, at the queryset boundary. |
| **Replaces** | “Load broad set then hope filters are correct” patterns. |
| **Must remain unchanged** | Public list filter contracts; filters may only narrow an already-authorized queryset. |

---

## 6. ViewSet Design

### Target State

The architecture does **not** replace DRF `ModelViewSet`.

Use composition:

```python
class ExampleViewSet(
    SlotyScopedResourceMixin,  # optional
    ModelViewSet,
):
    pass
```

- The mixin is **optional**.
- Endpoints without resource authorization may continue using normal DRF classes.
- Do not override `get_queryset()` to restart from `Model.objects` on a secured ViewSet.
- Narrow only through the secured queryset hook / DRF filter backends.

Also valid (spine v1 without v2 resource config):

```python
class ExampleViewSet(
    ClubScopedViewMixin,
    ModelViewSet,
):
    pass
```

### Current State

- `SlotyScopedResourceMixin` and `ClubScopedViewMixin` exist.
- Courts, Bookings, Transactions, Settlements, and Audit compose the v2 mixin.
- Settlements and Audit use `authorization_scope = ResourceScope.CLUB` (never court).
- Dashboard uses `ClubScopedViewMixin` + `SlotyBasePermission` (read model, no resource table).
- Reports uses `ClubScopedViewMixin` + `SlotyBasePermission` (read model, no resource table).
- Legacy domains (Club memberships) compose `ClubScopedAccessMixin`.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Keep DRF idioms; make security opt-in and composable. |
| **Replaces** | Custom base ViewSet forks of DRF. |
| **Must remain unchanged** | DRF lifecycle; mixins attach context before permission checks. |

---

## 7. Model Contract Rules

### Target State

Models that require authorization scope should declare:

- where their **club** boundary comes from
- where their **court** boundary comes from (if any)
- required relation loading (`select_related` / `prefetch_related`)

Examples:

| Model | Club path | Court path | Default |
| :--- | :--- | :--- | :--- |
| `Court` | `self` club via `"club": {"path": "club"}` | `"court": {"path": "self"}` | `court` |
| `CourtWorkingHour` | `court__club` | `court` | `court` |
| `Booking` | `club` | `court` | `court` |
| `BookingAttempt` | `club` | `court` | `court` |
| `Transaction` | `club` | `court` | `court` |
| `TransactionAttempt` | `club` | `court` | `court` |
| `Settlement` | `club` | — (none) | `club` |
| `ClubPlayer` (target) | `club` | — | `club` |

### Current State

- Courts already declare the contract.
- Bookings (`Booking`, `BookingAttempt`) declare the contract.
- ClubPlayer already declares the club-only contract.
- Transactions (`Transaction`, `TransactionAttempt`) declare the contract.
- Settlements (`Settlement`) declare the club-only contract. Collector visibility is applied in `filter_scoped_queryset()`, not as a court scope.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Security requirements become visible on the model, not hidden in ViewSets. |
| **Replaces** | Implicit knowledge of which FK is the tenant boundary. |
| **Must remain unchanged** | Existing DB schema until a migration task adds FKs/config. |

---

## 8. Domain Authorization Rules

### Target State

**Do not** create domain authorization modules for normal scope resolution.

Wrong:

```text
bookings/authorization.py
  if user can access court   ← spine already owns this
```

**Allowed** — business-specific rules the role matrix cannot express:

- `manager_can_change_pricing`
- Staff `created_by` / creator visibility restriction (transactions)
- Settlement approval / collector / self-approval rules

**Rule:**

> Authorization Spine owns **WHERE and WHO**.
> Domain authorization owns **special business conditions**.

### Current State

- `apps/courts/authorization.py` — pricing delegation flag only ✅
- `apps/transactions/authorization.py` — creator/court business rules ✅
- `apps/settlements/authorization.py` — collector/approval business rules ✅
- No bookings domain authorization module for court scoping (legacy access context still used)

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Prevent every domain re-implementing club/court filtering. |
| **Replaces** | Permission helpers that mix matrix decisions with queryset scoping. |
| **Must remain unchanged** | Existing business-rule modules until their owning domain migration explicitly moves any *scope* parts into the spine. |

---

## 9. Role Management Rules

### Target State

Never:

```python
role == "STAFF"
```

Never scatter hardcoded:

```text
"OWNER" / "MANAGER" / "STAFF"
```

Use centralized enums/constants (`Role`, `ClubMembership.Role`).

Finding hardcoded role/status/permission strings is a **refactoring trigger**:

1. Search all usages
2. Centralize the constant
3. Replace usages
4. Run affected tests

### Current State

- `Role` in `apps/common/authorization/roles.py` grounds values in `ClubMembership.Role`.
- Legacy and spine code generally use enums; any new string literals are a smell.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Prevent drift and typo-based auth bugs. |
| **Replaces** | Ad-hoc string comparisons. |
| **Must remain unchanged** | Stored DB role values (`OWNER` / `MANAGER` / `STAFF`) — only call sites centralize. |

---

## 10. Domain Scope Rules

### Target State

#### Bookings

- **Scope:** **Club + Court**
- **Staff:** Assigned courts only.
- **Not creator-restricted:** Bookings are operational court reservations, not user-owned. Any staff member assigned to a court can manage all bookings on that court regardless of who created them (`created_by`).
- **BookingAttempt Scope:** **Club + Court + attempted_by (for Staff)**. Tracks operational sync intents and rejected offline attempts; staff can view and dismiss only their own rejected attempts.
- **Identity Linkage Strategy:** `Booking.customer_name` and `Booking.customer_phone` remain **permanent immutable snapshots** for legal, financial, and audit integrity (audit trail, dashboard calendar, transaction receipts, search — reviewed against actual usage, not kept out of migration caution). `Booking.club_player` (nullable FK to `players.ClubPlayer`) links the booking to customer identity; there is no direct `player_profile` FK.
- **Phased Migration:**
  - **Phase A (Complete):** Identity linkage (`club_player` FK, backend auto-resolution in `create_booking()`, snapshot preservation, zero API breaks, zero authorization changes).
  - **Phase B (Complete):** Authorization Spine migration (`SlotyScopedResourceMixin`, `SlotyBasePermission`, `authorization_config`, fail-closed HTTP 404 for out-of-scope bookings).
  - **Phase C (Future):** Legacy cleanup (deprecate `ClubScopedAccessMixin` booking helpers once Club memberships no longer need them).
- See [`ADR-002`](adr/ADR-002-booking-identity-and-authorization-migration.md) and [`booking-migration-audit-v1.md`](booking-migration-audit-v1.md).

#### Transactions

- Scope: **Club + Court**
- **Plus** Staff creator restriction (business rule)

#### Settlement / Current Custody

- Scope: **Club + Collector**
- **Never Court**

#### Reports

- Every report must explicitly declare its aggregation scope from source domains.
- **Court Usage (current):** Club + Court via Booking/Court `authorization_config`. Admin/Owner/Manager see all club courts. Staff denied. Paid amounts are annotations on authorized bookings — not Transaction collector scope, not Settlement/custody.
- Optional `court` / `staff` query filters may only narrow. No export path; JSON uses the same authorized querysets.

#### Audit

- **Scope:** **Club only**
- **WHO:** Platform Admin / Owner / Manager (`list`, `retrieve`). Staff are denied.
- **Never Court** as a security boundary — `AuditLog.court` is nullable event metadata (membership deletes, some settlements).
- **Never Collector** — Settlement collector rules must not be applied to audit rows.
- **Not actor-scoped** — club-authorized readers see every actor in the club.
- Cross-domain event categories (Booking, Transaction, Settlement, ClubMembership) share this club read boundary. They do not inherit the originating domain's court/collector scope.

#### ClubPlayer (future)

- Scope: **Club**
- No cross-club read/write of another club’s naming

### Current State

- Courts: Club → Court (v2) ✅
- Bookings: Club → Court (v2) ✅; not creator-scoped; BookingAttempt Staff `attempted_by` narrowing in the ViewSet
- Transactions: Club → Court (v2) ✅ plus Staff collector `created_by` / cancel-own (except Platform Admin)
- Settlements: Club + Collector (v2 club queryset) ✅ plus collector narrowing / settle-authority in the domain module; never court
- Dashboard: Spine read-model (ClubScopedViewMixin + matrix); aggregations follow source-domain boundaries ✅
- Audit: Club only (v2 club queryset) ✅; Staff matrix-denied; not court- or collector-scoped
- Reports: Spine read-model (ClubScopedViewMixin + matrix); Court Usage Club + Court; Staff matrix-denied ✅

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Operational resources and financial custody have different boundaries; mixing them causes data leaks or blocked ops. |
| **Replaces** | Informal “staff sees their stuff” rules that confuse court with collector. |
| **Must remain unchanged** | Custody never becomes court-scoped; booking staff visibility stays court-assigned, not creator-only. |

---

## 11. Player Architecture

### Target State (Achieved)

```text
PlayerProfile
  phone_number UNIQUE   ← identity key (same phone = same person; different phone = different person; no merging)
  full_name             ← player's own preferred personal name (not club-controlled)
  verified
  user FK nullable      ← account may not exist yet
  created_at / updated_at
        │
        │
ClubPlayer               ← immutable versioned historical identity
  club_id
  player_profile_id
  display_name
  player_number
  previous_version_id    ← chain; NULL on first version
  is_current_version     ← exactly one True per (club, player_profile)
  created_at             ← insert only; no updated_at, no last_used_at
  UNIQUE (club_id, player_profile_id) WHERE is_current_version = True
        │
        ▼
Booking.club_player_id   ← exact version at booking time
                           last-used version = this FK on the latest booking
                           for the same player_profile_id at this club
```

**Rules:**

- A player can exist **without** an account (`user=NULL`, `verified=false`).
- Phone is the global identity key. A phone change is a new `PlayerProfile`.
- Same person, different club naming is valid (and expected). `PlayerProfile.full_name` is never auto-synchronized with `ClubPlayer.display_name`.
- Clubs cannot edit each other's `ClubPlayer` rows.
- **`ClubPlayer` is never edited in place and never soft-deleted.** Identity changes go through `create_club_player_version()`. Recency is not stored on the row.
- `Booking.club_player` stores the exact version used. New bookings default to the current version. Historical version selection is explicit (`club_player_id`). Last-used / recommended version is the `club_player` on the latest booking for that `player_profile_id`.
- Booking migration path:

```text
Booking Phase A:    customer_name / customer_phone snapshots + club_player FK
Sprint 3:           ClubPlayer versioning + current-version booking resolution
Sprint 4:           consumers read ClubPlayer; snapshots remain as write/historical contracts
Booking Phase B:    authorization spine; then snapshot column removal
```

### Current State

- `PlayerProfile` and `ClubPlayer` are implemented in `apps/players/`.
- `ClubPlayer` is on Authorization Spine v2 (`authorization_config`, `SlotyScopedResourceMixin`, `default_scope="club"`).
- `PlayerProfile` is a global model — no `authorization_config`. Club membership gates API access; list is restricted to club-linked profiles.
- Player Identity Foundation complete: models, services, API, tests, and AGENTS.md are in place.
- Booking Identity Linkage (Phase A) complete: `Booking.club_player` (nullable FK, no direct `player_profile` FK) is populated by `create_booking()`.
- **Sprint 3 complete:** `ClubPlayer` has `previous_version`, `is_current_version`, `created_at`; unique current version per `(club, player_profile)`; `create_club_player_version()`; booking default uses current version; optional historical `club_player_id`; last-used version is derived from the latest booking; player-profile search returns versions + `recommended_club_player_id`. No PATCH. No soft delete. No `last_used_at`. No `updated_at` on ClubPlayer.
- **Sprint 4 complete:** operational consumers read ClubPlayer via `apps.bookings.identity` (exact booking-time version, snapshot fallback). `customer_name` / `customer_phone` remain on Booking for create/PATCH/idempotency/audit JSON/BookingAttempt. `Booking.club_player` still nullable.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Support walk-in customers and later account linking without per-club phone silos. |
| **Replaces** | Booking-only denormalized customer fields as the person model. |
| **Must remain unchanged** | Existing booking create/list contracts until an explicit players + bookings migration; do not delete customer fields in the foundation phase. |

---

## 12. Migration Strategy

### Target order

```text
Documentation lock                          ← this document + AGENTS pointers
        ↓
Player identity foundation                  ← PlayerProfile + ClubPlayer
        ↓
Authentication improvements                 ← password change; JWT authority docs
        ↓
Bookings migration ✅                       ← spine + ClubPlayer link
        ↓
Dashboard ✅                                ← read-model Spine + source querysets
        ↓
Audit ✅                                        ← club-only Spine v2 read resource
        ↓
Reports ✅                                      ← read-model Spine + source querysets
        ↓
Remove leftover Clubs legacy                    ← ClubAccessContext / ClubScopedAccessMixin
```

### Already completed (mark and preserve)

| Domain | Status | Notes |
| :--- | :--- | :--- |
| Courts | ✅ | Spine v2 resource query |
| Transactions | ✅ | Spine v2 club+court queryset; Staff collector `created_by`; cancel-own except Platform Admin; Settlement custody stays club+collector |
| Settlements | ✅ | Spine v2 club-only queryset; collector narrowing + settle-authority in domain module; never court; Transaction API stays club+court |
| Identity Foundation | ✅ | `PlayerProfile` + `ClubPlayer` in `apps/players/`; `ClubPlayer` on Spine v2 |
| Booking Identity Linkage (Phase A) | ✅ | `Booking.club_player` FK + `create_booking()` auto-resolution |
| Bookings authorization (Phase B) | ✅ | Spine v2 club+court queryset; BookingAttempt Staff `attempted_by`; legacy layer kept for unmigrated domains |
| Dashboard | ✅ | Read-model Spine (`ClubScopedViewMixin` + `DashboardViewSet` matrix); aggregations from source-domain querysets; public availability anonymous |
| Audit | ✅ | Spine v2 club-only queryset; Staff matrix-denied; court/collector scopes from other domains are not applied; historical rows unchanged |
| Reports | ✅ | Read-model Spine (`ClubScopedViewMixin` + `CourtUsageReportViewSet` matrix); Court Usage Club+Court source querysets; Staff matrix-denied; no export path |

### Hard migration rules

1. **One domain at a time.**
2. **Behavioral parity required** — do not “clean up” product rules while migrating.
3. Do not port sync/`last_sync_at` side effects into spine mixins.
4. Do not mass-migrate because primitives exist.
5. Do not invent a second `authorization_config` shape.
6. Do not create Staff/Owner/Manager profile tables during player work.

### Current State

- Documentation lock is in progress via this file.
- Player foundation not started.
- Legacy access layer still required for unmigrated domains.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Gradual migration avoids breaking production ops while converging on one spine. |
| **Replaces** | Dual engines (legacy + spine) over time. |
| **Must remain unchanged** | Completed domain migrations’ external API behavior unless a task explicitly changes product rules. |

---

## 13. Resource URL Rule

### Target State

When a resource has a security boundary, prefer exposing it in the URL:

```text
URL → Authorization Resolver → Resource Scope
```

Examples:

- Court-scoped booking detail (future preference):
  `/api/v1/clubs/{club_slug}/courts/{court_id}/bookings/{booking_id}/`
- Club-only settlement (correct):
  `/api/v1/clubs/{club_slug}/settlements/`

Do **not** force court into every URL.

### Current State

- Club slug is the primary URL tenant boundary today.
- Nested court paths exist for some court-owned resources.
- Full URL redesign is not required for documentation lock.

### Migration Notes

| | |
| :--- | :--- |
| **Why** | Deterministic scope resolution reduces hidden trust of body/query IDs. |
| **Replaces** | Ambiguous endpoints where court is only implied. |
| **Must remain unchanged** | Existing public routes until a deliberate API migration. |

---

## Appendix A — Quick Decision Guide

| Question | Owner |
| :--- | :--- |
| Is the password/token valid? | Authentication |
| Is this user in this club with which role? | Authorization Spine |
| Which court rows may they see? | Resource scope (`COURT`) |
| May this manager change pricing? | Domain business rule |
| May staff see only their collected payments? | Domain business rule |
| How much is remaining on a booking? | Domain service |
| Who is phone `010…` globally? | `PlayerProfile` (future) |
| What jersey name does Club A use? | `ClubPlayer` (future) |

---

## Appendix B — Related Documents

- Root guide: [`AGENTS.md`](../../AGENTS.md)
- Common / spine implementation notes: [`apps/common/AGENTS.md`](../../apps/common/AGENTS.md)
- Accounts identity/auth: [`apps/accounts/AGENTS.md`](../../apps/accounts/AGENTS.md)
- Players customer identity: [`apps/players/AGENTS.md`](../../apps/players/AGENTS.md)
- Clubs membership + legacy access: [`apps/clubs/AGENTS.md`](../../apps/clubs/AGENTS.md)
- Courts (v2 migrated): [`apps/courts/AGENTS.md`](../../apps/courts/AGENTS.md)
- Bookings (Spine v2 migrated; identity snapshots retained): [`apps/bookings/AGENTS.md`](../../apps/bookings/AGENTS.md)
- Transactions (Spine v2 migrated; collector/cancel rules in domain module): [`apps/transactions/AGENTS.md`](../../apps/transactions/AGENTS.md)
- Settlements (Spine v2 migrated; collector/settle rules in domain module; never court): [`apps/settlements/AGENTS.md`](../../apps/settlements/AGENTS.md)
- Dashboard (Spine read-model migrated; public availability anonymous): [`apps/dashboard/AGENTS.md`](../../apps/dashboard/AGENTS.md)
- ADR-001 (Player Identity & Booking Link): [`ADR-001`](adr/ADR-001-player-identity-and-booking-link.md)
- ADR-002 (Booking Identity & Authorization Migration): [`ADR-002`](adr/ADR-002-booking-identity-and-authorization-migration.md)
- Booking Migration Audit: [`booking-migration-audit-v1.md`](booking-migration-audit-v1.md)
