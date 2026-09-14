# ADR-002: Booking Identity Linkage and Authorization Migration Strategy

**Status:** Accepted
**Date:** 2026-09-13
**Deciders:** Principal Backend Architect, Domain Architect
**Consulted:** Security Architecture v1, ADR-001

---

## 1. Context

The Sloty backend has established the foundational security and customer identity primitives:
- **Authorization Spine v2** (`apps/common/authorization/`): Context resolution, `SlotyScopedResourceMixin`, `SlotyBasePermission`, declarative `authorization_config`.
- **Identity Foundation** (`apps/players/`): `PlayerProfile` (global customer identity anchored on phone) and `ClubPlayer` (club-local player representation).

The next major milestone is the **Booking Domain Migration** (`apps/bookings/`).

`Booking` is the core operational entity of Sloty, interconnected with slot availability, pricing periods, financial transactions, settlements, calendar dashboard, and offline PWA synchronization. Currently, `apps/bookings/`:
1. Relies entirely on denormalized string and phone fields (`customer_name`, `customer_phone`) with no linkage to `PlayerProfile` or `ClubPlayer`.
2. Runs on legacy access classes (`ClubScopedAccessMixin`, `ClubAccessContext`, `CanManageClubBookings`, `CanManageBookingAttempts`) with scattered court and role checks across serializers and services.

To ensure stability, business continuity, and zero regression during migration, this ADR formally freezes the architecture decisions for Booking identity linkage and authorization migration.

---

## 2. Decisions

### Decision 1 — Booking Identity Model & Immutable Snapshots

#### Data Model Evolution:
- **Current State:**
  ```text
  Booking:
    customer_name  (CharField)
    customer_phone (PhoneNumberField)
  ```
- **Future Target State:**
  ```text
  Booking:
    customer_name  (CharField - Snapshot)
    customer_phone (PhoneNumberField - Snapshot)
    player_profile (Nullable FK -> players.PlayerProfile, on_delete=SET_NULL)
    club_player    (Nullable FK -> players.ClubPlayer, on_delete=SET_NULL)
  ```

#### Why Historical Snapshots Stay Forever:

> [!IMPORTANT]
> **Permanent Invariant:** Historical booking data must **never** mutate when customer identity or display names change.

**Concrete Example:**
1. A booking is created on 2026-05-20:
   - `customer_name`: "Ahmed Ali"
   - `customer_phone`: "+201012345678"
2. Months later, the club updates this customer's jersey or display name in `ClubPlayer`:
   - `display_name`: "Ahmed Salah"
3. The historical booking created on 2026-05-20 **must still display "Ahmed Ali"**.
4. Legal receipts, audit trails, and historical match logs represent agreements made at that point in time and must remain immutable.

Therefore, `customer_name` and `customer_phone` remain as permanent, immutable snapshot fields.

---

### Decision 2 — Player Identity Resolution & Offline Safety

#### Booking Creation Flow:

```text
Booking Request (POST /clubs/{slug}/bookings/)
        │
        ▼
   Payload contains:
   customer_phone, customer_name, court, start_time, end_time
        │
        ▼
   Booking Service (create_booking inside transaction.atomic)
        │
        ├─► find_or_create_player_profile(phone_number, full_name)
        │
        ├─► get_or_create_club_player(club, player_profile, display_name)
        │
        ▼
   Create Booking:
   - customer_name = payload customer_name (Snapshot)
   - customer_phone = payload customer_phone (Snapshot)
   - player_profile = resolved PlayerProfile FK
   - club_player = resolved ClubPlayer FK
```

#### Client Contract Rule:
- The frontend **does NOT send** `player_profile_id` or `club_player_id` for standard booking creation.
- **Rationale:**
  1. **Offline PWA Support:** In offline mode, staff can book a court for a walk-in player without network connectivity. If `club_player_id` were required, offline creation would fail for new players.
  2. **Fast Walk-in Workflow:** Staff at court reception simply enter phone number and name.
  3. **Encapsulation:** The backend owns identity reconciliation; frontend clients are spared from managing customer identity lifecycles.

---

### Decision 3 — Booking Security Boundary

#### Resource Scope:
```text
Booking Scope:
Club + Court
```
**NOT:** `Club + Court + Creator`

#### Operational Invariant:
- Bookings represent court operational reservations, **not private records owned by the user who created them**.
- Any staff member assigned to Court A can view, reschedule, complete, cancel, or mark no-show for **any** booking on Court A, regardless of who created it (`created_by`).
- Shifts change throughout the day; morning staff and evening staff share operational authority over court bookings.

---

### Decision 4 — BookingAttempt Security Boundary

#### Resource Scope:
```text
BookingAttempt Scope:
Club + Court + attempted_by (for Staff)
```

#### Invariant Separation:
- `BookingAttempt` represents user-initiated operational synchronization actions (offline submissions, conflict logs, and rejected intents).
- Staff members can only view and dismiss **their own** rejected attempts (`attempted_by = request.user`). Owners and managers have club-wide visibility.
- **Rule:** Do not merge `Booking` and `BookingAttempt` authorization rules. They serve different operational purposes.

---

### Decision 5 — Authorization Spine Migration Pattern

#### Technology Transition:
- **Current (Legacy):**
  - `ClubScopedAccessMixin`
  - `CanManageClubBookings`
  - `CanManageBookingAttempts`
  - Queryset scoping via `ClubAccessContext.scoped_bookings_queryset()`
- **Future Target:**
  - `SlotyScopedResourceMixin`
  - `SlotyBasePermission`
  - Centralized matrix permissions (`ROLE_PERMISSIONS` in `apps/common/authorization/matrix.py`)
  - Declarative model `authorization_config` on `Booking` and `BookingAttempt`

#### Target Request Flow:

```text
HTTP Request
  │
  ▼
Authentication Layer (JWT)
  │
  ▼
Authorization Context (resolve_club_scope)
  │
  ▼
Resolve Scope (Club + Court)
  │
  ▼
Build Authorized QuerySet (get_scoped_queryset via Spine v2)
  │
  ▼
Business Filters (django-filter FilterSet: status, date, search)
  │
  ▼
Pagination
  │
  ▼
Service Layer (State transitions, pricing, availability)
  │
  ▼
Response Serialization
```

---

### Decision 6 — Separation of Responsibilities

The following architectural boundary is locked:

| Layer | Strict Ownership | Must NOT Own |
|:---|:---|:---|
| **Authorization Layer** (Spine) | - "Who is the user?"<br>- Club membership validation<br>- Court/resource scoping (`ResourceScope.COURT`)<br>- QuerySet security boundary (fail-closed HTTP 404) | Business workflows, price calculations, state transitions. |
| **Services Layer** (`apps/bookings/services.py`) | - Booking state machine transitions<br>- Agreed price snapshot calculations<br>- Slot availability and overlap protection<br>- Recurrence arithmetic<br>- Cancellation refund policies | Checking user roles (`if role == 'STAFF'`), inspecting court assignments (`can_access_court`), or broad queryset filtering. |
| **Serializers Layer** (`apps/bookings/serializers.py`) | - Request payload shape validation<br>- Datetime and slot duration format validation<br>- Response serialization | Permission checks, court access checks. |

---

### Decision 7 — No Domain Authorization Module Unless Required

- **Do NOT create** `apps/bookings/authorization.py` for standard access rules.
- The Authorization Spine natively owns:
  - Club scope boundary
  - Court scope boundary
  - Staff court assignment filtering
  - Centralized role-to-action matrix decisions
- A domain authorization helper is permitted **only** for genuine business conditions that the matrix cannot express.
  - **Allowed:** `Staff cancellation requires a reason` (`actor_requires_staff_cancel_reason`).
  - **Not allowed:** `Staff can access court` (this belongs exclusively to the spine).

---

### Decision 8 — Phased Migration Strategy

To eliminate operational risk, Booking migration is strictly decoupled into three sequential phases:

```text
┌─────────────────────────────────────────────────────────────┐
│ Phase A — Identity Linkage (Next Sprint)                    │
├─────────────────────────────────────────────────────────────┤
│ • Add nullable player_profile and club_player FKs to       │
│   Booking model.                                            │
│ • Resolve/create identities in create_booking() service.    │
│ • Preserve customer_name and customer_phone snapshots.      │
│ • Zero API breaking changes. Zero authorization changes.    │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase B — Authorization Spine Migration (Complete)          │
├─────────────────────────────────────────────────────────────┤
│ • Declare authorization_config on Booking & BookingAttempt. │
│ • Reconcile matrix.py (add cancellation_preview, reconcile  │
│   partial_update for Owner/Manager/Staff).                  │
│ • Switch BookingViewSet to SlotyScopedResourceMixin and     │
│   SlotyBasePermission.                                      │
│ • Refactor get_lifecycle_booking to use self.get_object().   │
│ • Eliminate court access checks from serializers/services.  │
│ • Add regression suite in tests/bookings/.                  │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase C — Legacy Cleanup                                    │
├─────────────────────────────────────────────────────────────┤
│ • Remove legacy booking helper methods from                 │
│   ClubAccessContext and apps/clubs/mixins.py.               │
│ • Delete CanManageClubBookings / CanManageBookingAttempts.  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Consequences

### Positive
- **Historical Consistency:** Guarantees historical booking integrity regardless of subsequent customer profile changes.
- **Offline Resilience:** Walk-in and offline booking workflows remain non-blocking and fully operational.
- **Fail-Closed Security:** Replaces HTTP 403 existence leaks with clean HTTP 404 scoping via the Authorization Spine v2.
- **Clean Architecture:** Removes scattered permission and court checks from serializers and services.

### Negative / Trade-offs
- `Booking` temporarily maintains both foreign keys and snapshot fields (deliberate design decision for historical data preservation).
- Booking creation performs profile lookup/creation within `transaction.atomic()`, handled cleanly via `find_or_create_player_profile()` and `get_or_create_club_player()`.

---

## 4. References

- [`docs/architecture/security-architecture-v1.md §10 & §11`](../security-architecture-v1.md)
- [`docs/architecture/booking-migration-audit-v1.md`](../booking-migration-audit-v1.md)
- [`docs/architecture/adr/ADR-001-player-identity-and-booking-link.md`](ADR-001-player-identity-and-booking-link.md)
- [`apps/bookings/AGENTS.md`](../../../apps/bookings/AGENTS.md)

---

## 5. Addendum — Phase A Implementation Refinement (2026-09-13)

**Status:** Applied during Booking Identity Finalization — Phase A implementation.

Decision 1's data model evolution (§2, Decision 1) proposed **two** nullable FKs on `Booking`:

```text
player_profile (Nullable FK -> players.PlayerProfile, on_delete=SET_NULL)
club_player    (Nullable FK -> players.ClubPlayer, on_delete=SET_NULL)
```

During Phase A implementation, this was refined to a **single** FK:

```text
club_player (Nullable FK -> players.ClubPlayer, on_delete=SET_NULL)
```

`Booking` does **not** carry a direct `player_profile` FK. `PlayerProfile` remains reachable transitively via `booking.club_player.player_profile`.

### Rationale

1. **Business identity framing.** The business identity of a booking is "a booking made by this club's representation of a player," not "a booking made by a global person." `ClubPlayer` is the correct direct relation; `PlayerProfile` is a supporting concept one hop away.
2. **No distinct invariant to protect.** `ClubPlayer.player_profile` is immutable after creation (`apps/players/AGENTS.md` Domain Invariant 6). A second FK on `Booking` could never diverge from `club_player.player_profile` in practice, so it would add schema surface without adding a real safeguard.
3. **Simpler invariant enforcement.** With one FK, the only cross-table invariant to enforce is `Booking.club_id == Booking.club_player.club_id` (see `apps/bookings/AGENTS.md`). Two FKs would have required either a second invariant (`club_player.player_profile_id == player_profile_id`, redundant by construction) or an unenforced assumption.
4. **Smaller resolution flow.** `resolve_booking_club_player()` only needs to persist one FK; `find_or_create_player_profile()` is still called internally (to resolve/create the global profile) but its result is not separately stored on `Booking`.

### What did not change

- Decision 2 (Player Identity Resolution & Offline Safety) — unchanged. The resolution flow still runs `find_or_create_player_profile()` before `get_or_create_club_player()`; the frontend still never sends `player_profile_id` / `club_player_id`.
- Phase C (legacy helper removal) remains blocked on Club memberships. Dead unused booking/audit/dashboard/report/settlement permission wrappers were deleted.

---

## 6. Addendum — ClubPlayer Historical Identity Foundation, Sprint 1 (2026-09-13)

**Status:** Applied. Scope: `apps/players/` only. Does **not** touch `Booking`, its serializers, ViewSets, or authorization.

A prior architecture review (Booking Identity Architecture Review — Final Design Validation, same date) evaluated removing `Booking.customer_name`/`customer_phone` entirely and found the blast radius too large for a single sprint (263 test references across 9 files, 10+ production consumer files, an idempotency-semantics redesign, and a public API-contract change documented in `README.md`). That removal was **deferred**, split from the prerequisite groundwork below, and **not** part of this addendum.

This addendum documents the prerequisite groundwork that sprint's review identified as blocking: making `ClubPlayer` itself an append-oriented historical identity record, independent of whatever happens to `Booking.customer_name`/`customer_phone` later.

### Decision

`ClubPlayer` rows are never edited in place after creation. The addendum §4 rationale ("`ClubPlayer.player_profile` is immutable after creation") is extended to **all** `ClubPlayer` identity fields (`club`, `player_profile`, `display_name`, `player_number`):

- `ClubPlayer.deleted_at` (nullable `DateTimeField`) — `NULL` = active/current identity; set = historical/superseded. Rows are never physically deleted.
- The prior unconditional `UNIQUE(club, player_profile)` becomes conditional: `UNIQUE(club, player_profile) WHERE deleted_at IS NULL` — at most one **active** row per pair; any number of historical (deleted) rows may coexist for the same pair.
- `apps.players.services.supersede_club_player()` is the only supported identity-change transition: soft-delete the current active row, create a new active row for the same `(club, player_profile)` pair.
- `get_or_create_club_player()` (used by `create_booking()`'s `resolve_booking_club_player()`) now matches only active rows and never resurrects a deleted one.
- `ClubPlayerViewSet` no longer exposes `update`/`partial_update` — no PATCH endpoint, no `ClubPlayerUpdateSerializer`, no matrix grant for any role. Enforced additionally at the model (`ClubPlayer.save()`) and queryset (`ClubPlayerQuerySet.update()`) layers as defense in depth.

### Consequence for Booking identity

`Booking.club_player` (added in the addendum above) inherits a stronger guarantee than originally specified: because the referenced `ClubPlayer` row can never change after a `Booking` is created — only be soft-deleted and superseded by a *different* row — `Booking.club_player_id` **is** the historical identity snapshot as of booking time, with no possibility of silent drift. This was implicit in the original Phase A design intent but not yet true in practice until this sprint (a `ClubPlayer` could previously be edited via PATCH, which would have silently changed how every referencing `Booking` displayed, had any consumer read through `club_player` — none did yet, so no observable bug occurred, but the gap existed).

### What did not change

- `Booking.customer_name` / `customer_phone` — untouched in Sprint 1. Sprint 3 still does not remove them.
- Booking authorization/ViewSets — untouched.

## 7. Addendum — ClubPlayer Versioned Identity, Sprint 3 (2026-09-14)

**Status:** Applied. Replaces the Sprint 1 soft-delete lifecycle.

### Decision

- `ClubPlayer` is an immutable versioned historical entity. No `deleted_at`. No soft delete. No `last_used_at`. No `updated_at`.
- Fields: `previous_version`, `is_current_version`, `created_at`.
- `UNIQUE(club, player_profile) WHERE is_current_version=True`.
- Phone change = new `PlayerProfile`. No merging, no identity transfer, no name-based matching.
- `PlayerProfile.full_name` (preferred personal name) is not auto-synchronized with `ClubPlayer.display_name`.
- Booking default resolution uses the current version. Optional `club_player_id` selects a historical version.
- Last-used / recommended version is the `club_player` on the latest `Booking` for that `player_profile_id` at the club.
- `customer_name` / `customer_phone` remain until consumer migration. `Booking.club_player` stays nullable.
- Authorization Spine and dashboard/transactions/settlements/reports/audit **authorization** are not migrated in this sprint.

### What this supersedes

Addendum §6 (`deleted_at`, `supersede_club_player()`, unique-active-where-not-deleted) is historical. Runtime behavior is the versioned model above.

## 8. Addendum — Consumer Migration To ClubPlayer Identity, Sprint 4 (2026-09-14)

**Status:** Applied.

### Decision

- Operational display and search read `Booking.club_player` (exact version at booking time) via `apps.bookings.identity`.
- Public API keys (`customer_name`, `customer_phone`, `booking_customer_*`) are unchanged.
- Snapshot columns remain for create/PATCH, idempotency, audit event JSON, and BookingAttempt payloads.
- `Booking.club_player` stays nullable; helpers fall back to snapshots.
- Reports do not display customer identity; no report identity change.
- Snapshot **removal** is complete (see Addendum 9). This addendum described the operational-read migration that preceded it.

### What this supersedes

Sprint 3 addendum's "consumers are not migrated" line. Runtime operational reads now go through ClubPlayer.

---

## 8. Addendum — Phase B Authorization Spine Migration (2026-09-14)

**Status:** Applied during Booking Authorization Spine Migration. Scope: Booking/BookingAttempt authorization only. Identity, snapshots, and URLs are unchanged.

### Decision

- `Booking` and `BookingAttempt` declare Spine v2 `authorization_config` with `default_scope="court"`.
- `BookingViewSet` and `BookingAttemptViewSet` compose `SlotyScopedResourceMixin` + `SlotyBasePermission`.
- Booking security boundary is Club + Court and is **not** creator-scoped.
- BookingAttempt keeps Staff `attempted_by=request.user` narrowing in `filter_scoped_queryset()`. Dismiss remains attempter-only for every role via `check_object_permission`.
- Matrix gained `cancellation_preview` and `partial_update` for Owner/Manager/Staff so existing PATCH and preview behavior is preserved.
- Lifecycle actions load the booking through `self.get_object()` on the secured queryset. Out-of-scope bookings return HTTP 404 instead of HTTP 403 (existence-leak fix). Create/slots/reschedule **target court** denials remain HTTP 403.
- `apps/bookings/authorization.py` was not created. Legacy `ClubAccessContext` remains for Club memberships.
- Phase C (delete leftover scoped-queryset helpers from the legacy Clubs layer) is **not** done. Unused migrated-domain permission wrappers were deleted.

---

## 9. Addendum — Booking Snapshot Column Removal (2026-09-14)

**Status:** Applied. Supersedes Decision 1's "snapshots stay forever" once ClubPlayer became immutable versions.

### Decision

ClubPlayer versions are the historical identity. Booking no longer stores `customer_name` / `customer_phone`.

```text
Booking.club_player_id  NOT NULL  on_delete=PROTECT
      ↓
ClubPlayer (immutable version)
      ↓
PlayerProfile (phone identity key)
```

- Walk-in create still accepts `customer_name` / `customer_phone` as resolution inputs. Optional `club_player_id` selects a historical version.
- Booking PATCH is notes-only. It does not mutate ClubPlayer identity.
- Idempotency stays keyed by `client_request_id` per club. BookingAttempt keeps submitted name/phone as payload evidence. Booking-level replay matches court, time, source, notes, PlayerProfile phone, and an explicit `club_player_id` when sent.
- New audit snapshots store ClubPlayer display at event time. Historical audit JSON is not rewritten.
- BookingAttempt `customer_*` fields remain as request payload evidence.

### What this supersedes

Decision 1's permanent Booking snapshot columns, and Sprint 4's nullable `club_player` + snapshot fallback.
