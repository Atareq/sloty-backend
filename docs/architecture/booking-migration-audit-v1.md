# Booking Domain Architecture Audit (v1)

**Status:** Historical audit (2026-09-13). Identity finalization later removed Booking snapshot columns and made `club_player` required. Phase B Authorization Spine migration is implemented. Do not treat the "currently unmigrated" / "snapshots remain" statements below as current runtime state — see `apps/bookings/AGENTS.md` and ADR-002 addendum §9.
**Date:** 2026-09-13
**Auditor:** Senior Backend Architect & Domain Architect
**Domain:** `apps/bookings/`
**Target Migration Path:** Authorization Spine v2 + Customer Identity (`ClubPlayer` / `PlayerProfile`)

---

## Executive Summary

This document presents a comprehensive, read-only architectural audit of the Sloty Booking domain (`apps/bookings/`) prior to starting the Booking migration sprints.

The Sloty backend modular monolith has already successfully migrated:
1. **Authorization Spine Foundation** (`apps/common/authorization/`)
2. **Courts Domain** (`apps/courts/` on Spine v2)
3. **Transactions Domain** (`apps/transactions/` on Spine v1)
4. **Settlements Domain** (`apps/settlements/` on Spine v1)
5. **Identity Foundation** (`apps/players/`: `PlayerProfile` + `ClubPlayer`)

The Booking domain is the operational core of Sloty. It manages time slot reservations, pricing snapshots, overlap protection, state machine transitions, recurrence, offline idempotency, and financial refund triggers.

Currently, `apps/bookings/` is **unmigrated** and runs on the legacy access engine (`ClubScopedAccessMixin`, `ClubAccessContext`, `CanManageClubBookings`). Furthermore, customer identity in bookings relies on **denormalized string and phone fields** (`customer_name`, `customer_phone`), with no foreign keys to `PlayerProfile` or `ClubPlayer`.

This audit establishes the baseline facts, maps dependencies, uncovers critical discrepancies (including matrix permission gaps and 403 vs 404 object isolation behaviors), and provides a low-risk, phased blueprint for the upcoming **Booking Migration**.

---

## 1. Booking Domain Map

### 1.1 Structural Inventory

```text
apps/bookings/
├── models.py          (389 lines)   — Booking, BookingAttempt
├── services.py        (2,012 lines) — Domain workflows, pricing, state machine, idempotency
├── serializers.py     (638 lines)   — Serializers for CRUD, actions, slots, and attempts
├── views.py           (397 lines)   — BookingViewSet, BookingAttemptViewSet
├── filters.py         (305 lines)   — BookingFilter, BookingAttemptFilter, hold expiry annotations
├── permissions.py     (18 lines)    — CanManageBookingAttempts, CanManageClubBookings re-export
├── urls.py            (98 lines)    — URL routing for bookings, attempts, actions, slots
├── admin.py           (79 lines)    — Django admin registrations
├── apps.py            (6 lines)     — BookingsConfig
├── AGENTS.md          (119 lines)   — Scoped domain guide
└── management/
    └── commands/
        └── expire_hold_bookings.py  — Background hold expiration worker
```

### 1.2 Models & Schema

#### `Booking` (`apps/bookings/models.py`)
- **Primary Keys & Boundaries:**
  - `id`: BigAutoField
  - `club`: FK to `clubs.Club` (`on_delete=CASCADE`, `related_name="bookings"`) — Primary tenant boundary.
  - `court`: FK to `courts.Court` (`on_delete=CASCADE`, `related_name="bookings"`) — Physical resource boundary.
- **Customer Identity (Denormalized Snapshots):**
  - `customer_name`: `CharField(max_length=255)`
  - `customer_phone`: `PhoneNumberField()`
- **Time & Pricing (Backend Authoritative):**
  - `start_time`: `DateTimeField()`
  - `end_time`: `DateTimeField()`
  - `total_price`: `DecimalField(max_digits=10, decimal_places=2, min_value=0.00)`
- **State Machine & Lifecycle:**
  - `status`: `CharField(max_length=32, choices=Status.choices, default=Status.HOLD, db_index=True)`
    - Statuses: `HOLD`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`
    - Blocking statuses: `HOLD`, `CONFIRMED`, `COMPLETED`, `NO_SHOW`
    - Non-blocking statuses: `CANCELLED`, `EXPIRED`
    - Locked statuses: `COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`
- **Recurrence & Origin:**
  - `source`: `MANUAL`, `ADMIN_CORRECTION`, `RECURRING`
  - `recurrence_status`: `ACTIVE`, `RENEWED`, `ENDED` (null for non-recurring)
  - `previous_recurring_booking`: Self-referential `OneToOneField(null=True, blank=True, on_delete=SET_NULL, related_name="next_recurring_booking")`
- **Audit & Offline Traceability:**
  - `client_request_id`: `UUIDField(null=True, blank=True, db_index=True)` — Idempotency key per club
  - `notes`: `TextField(blank=True)`
  - Reasons: `cancellation_reason`, `no_show_reason`, `reschedule_reason`
  - Lifecycle Timestamps: `completed_at`, `cancelled_at`, `no_show_at`, `expired_at`, `created`, `modified`
  - Actors: `created_by` (FK `User`), `last_status_changed_by` (FK `User`), `last_status_changed_by_type` (`INTERNAL_USER`, `SYSTEM`)
- **Model Constraints:**
  - Check constraints for recurrence integrity (`booking_recurring_source_requires_status`, `booking_non_recurring_status_null`, `booking_non_recurring_previous_null`)
  - Unique constraint: `UNIQUE(club, client_request_id)` where `client_request_id IS NOT NULL`

#### `BookingAttempt` (`apps/bookings/models.py`)
- Persistent audit ledger for offline requests, synchronization attempts, and conflict resolution.
- Key fields: `club`, `court`, `attempted_by` (FK `User`), `booking` (nullable FK to `Booking`), `client_request_id`, `customer_name`, `customer_phone`, `requested_start`, `requested_end`, `requested_at`, `requested_source`, `requested_recurring`, `outcome` (`SUCCESS`, `REJECTED`), `failure_code`, `failure_details` (JSON), `resolution` (`UNRESOLVED`, `DISMISSED`, `RESOLVED`).

### 1.3 Service Layer Functions (`apps/bookings/services.py`)

- **Booking Creation:** `create_booking()`, `validate_booking_duration()`, `calculate_booking_price()`, `validate_no_availability_conflict()`, `validate_can_start_recurrence()`.
- **Lifecycle Transitions:** `cancel_booking()`, `complete_booking()`, `no_show_booking()`, `reschedule_booking()`, `expire_booking()`, `end_booking_recurrence()`.
- **Hold Expiry Worker:** `expire_due_hold_bookings()`, `expire_booking()`.
- **Slot Availability Engine:** `generate_booking_slots()`, `find_new_recurrence_conflict()`, `build_slot_timeline()`, `format_slot_label()`.
- **Idempotency & Attempts:** `resolve_idempotent_booking_request()`, `resolve_idempotent_booking_attempt()`, `create_success_booking_attempt()`, `create_rejected_booking_attempt()`, `dismiss_booking_attempt()`.
- **Cancellation & Refunds:** `build_cancellation_preview()`, `calculate_cancellation_refund()`, `validate_refund_fields()`.
- **Audit Logging:** `booking_audit_snapshot()`, calls to `apps.audit.services.record_audit_log()`.

### 1.4 Dependency Map

```text
               ┌───────────────┐
               │     Club      │ (Tenant Boundary)
               └───────┬───────┘
                       │
         ┌─────────────┴─────────────┐
         │                           │
         ▼                           ▼
┌─────────────────┐         ┌─────────────────┐
│      Court      │         │   BookingAttempt │
└────────┬────────┘         └────────┬────────┘
         │                           │
         ▼                           ▼
┌─────────────────────────────────────────────┐
│                   Booking                   │
├─────────────────────────────────────────────┤
│ - customer_name, customer_phone (Snapshot)  │
│ - total_price, start_time, end_time         │
│ - status, source, recurrence_status         │
└───────┬─────────────────────────────┬───────┘
        │                             │
        ▼                             ▼
┌───────────────┐             ┌───────────────┐
│  Transaction  │             │   AuditLog    │
│ (Payments &   │             │ (Append-Only  │
│   Refunds)    │             │    Trail)     │
└───────────────┘             └───────────────┘
        │
        │ [Future Target Link]
        ▼
┌─────────────────────────────────────────────┐
│                 ClubPlayer                  │
│       (apps/players/models.py)              │
├─────────────────────────────────────────────┤
│ - club_id                                   │
│ - player_profile_id                         │
│ - display_name, player_number               │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│                PlayerProfile                │
│       (apps/players/models.py)              │
├─────────────────────────────────────────────┤
│ - phone_number UNIQUE (Global Identity Key) │
│ - user_id (Nullable FK to User)             │
│ - full_name, verified                       │
└─────────────────────────────────────────────┘
```

---

## 2. Current Authorization Flow

### 2.1 Trace of Current Request Flow

```text
HTTP Request
  │
  ▼
URL Routing: /api/v1/clubs/{club_slug}/bookings/
  │
  ▼
Authentication Layer (DRF JWT Authentication)
  │ Resolves request.user
  │
  ▼
Access Context Construction (ClubScopedAccessMixin)
  │ Calls: access = self.get_access_context()
  │ Instantiates: ClubAccessContext(request.user, club)
  │ Evaluates: user active memberships, staff_court_ids, is_owner, is_manager, is_staff
  │
  ▼
View Permission Check (CanManageClubBookings)
  │ has_permission(): returns access.has_any_club_access()
  │ [Gives green light to any authenticated user with an active membership in the club]
  │
  ▼
QuerySet Resolution (BookingViewSet.get_queryset())
  │ Calls: access.scoped_bookings_queryset()
  │ Under the hood: Court.objects.filter(club=club) restricted to staff_court_ids if staff
  │ Returns: Booking.objects.filter(court__in=scoped_courts)
  │ Annotates: paid_amount and hold_expires_at
  │
  ▼
Action Execution & Validation
  ├─ For create():
  │    └─ Serializer validate() calls access.can_create_booking_for_court(court)
  │       [Raises PermissionDenied(403) inside serializer]
  │
  └─ For lifecycle actions (cancel, complete, reschedule, etc.):
       ├─ get_lifecycle_booking() queries:
       │    Booking.objects.filter(pk=id, club=access.club)  <-- NOTE: Omits court filter!
       └─ Service validate_booking_for_lifecycle_action() calls:
            access.can_change_booking_status(booking)
            [Raises PermissionDenied(403) inside service layer if staff is not assigned to court]
```

### 2.2 Critical Authorization Discrepancies & Deficiencies

1. **Four-Way Authorization Scattering:**
   Authorization is fragmented across:
   - Permissions: `CanManageClubBookings` (binary check).
   - ViewSet: `get_lifecycle_booking()` (checks club, ignores court).
   - Serializer: `BookingCreateSerializer.validate()` (checks `access.can_create_booking_for_court(court)`).
   - Service: `services.py` (`validate_booking_for_lifecycle_action`, `reschedule_booking`).

2. **Object Permissions are Bypassed:**
   `BookingViewSet` custom actions (`cancel`, `complete`, `no_show`, `reschedule`, `expire`, `end_recurrence`) do **not** invoke `self.check_object_permissions(request, booking)`. They bypass standard DRF object security entirely and pass the object directly into services.

3. **Existence Leak (403 Forbidden vs. 404 Not Found):**
   When Staff assigned to Court 1 attempts to invoke a lifecycle action on a booking on Court 2 (same club):
   - `get_lifecycle_booking()` locates the booking because it filters only by `club=access.club`.
   - The service then raises `PermissionDenied("You cannot change this booking status.")`.
   - The client receives **HTTP 403 Forbidden**.
   - **Leak:** The caller now knows a booking with that ID exists on another court. Under the Authorization Spine v2 contract, the query must be bounded to the authorized resource scope first, returning **HTTP 404 Not Found**.

4. **Matrix Configuration Drift:**
   In `apps/common/authorization/matrix.py`:
   - `Role.ADMIN` has `"BookingViewSet": {"list", "retrieve", "create", "update", "partial_update", "destroy", "cancel", ...}`.
   - `Role.OWNER`, `Role.MANAGER`, and `Role.STAFF` have `"cancel", "complete", ...` but **lack `"update"` and `"partial_update"`**.
   - In active production code and tests (`test_accepted_attempt_remains_accepted_after_booking_cancel_and_edit`), **Staff actively executes `PATCH /bookings/{id}/`** to update customer notes and names.
   - If `BookingViewSet` were switched to `SlotyBasePermission` today without updating the matrix, staff patching would immediately break with 403 Forbidden.

---

## 3. Booking Security Boundary

### 3.1 Authoritative Resource Scope

The target security boundary for bookings is:

```text
Booking Scope:
Club + Court
```

### 3.2 Invariant Analysis

- **Not Creator-Restricted:**
  Bookings are **not** owned by the staff user who created them. A booking represents a physical time slot on a court. Any staff member assigned to that court must be able to view, complete, cancel, or report a no-show for that slot (e.g. shift handovers, morning vs. evening staff).
- **BookingAttempt Divergence:**
  `BookingAttempt` has a different boundary:
  - For Owners / Managers: `Club + Court`.
  - For Staff: `Club + Court + Creator` (`attempted_by = request.user`). Staff can only view and dismiss their own unresolved rejected offline attempts.

---

## 4. Customer Identity Analysis

### 4.1 Search Inventory of Customer Fields

The fields `customer_name` and `customer_phone` are referenced across 180+ locations in the codebase:

| Subsystem / App | Location | Usage |
|:---|:---|:---|
| `apps/bookings` | `models.py` | Defined on `Booking` (lines 61-62) and `BookingAttempt` (lines 257-258). |
| `apps/bookings` | `serializers.py` | Included in `BookingCreateSerializer`, `BookingUpdateSerializer`, `BookingListSerializer`, `BookingDetailSerializer`, `BookingAttemptListSerializer`. |
| `apps/bookings` | `services.py` | Used in `create_booking()`, `booking_audit_snapshot()`, and idempotency matching: `booking_matches_client_request()`, `booking_attempt_matches_client_request()`. |
| `apps/bookings` | `filters.py` | `BookingFilter.filter_search` performs `icontains` on `customer_name` and runs Egyptian phone normalization on `customer_phone`. |
| `apps/transactions` | `serializers.py`, `filters.py`, `services.py` | Read-only denormalization: `booking_customer_name`, `booking_customer_phone` for receipts, transaction lists, and financial exports. |
| `apps/settlements` | `serializers.py`, `services.py` | Denormalized in settlement item lines: `booking_customer_name`. |
| `apps/dashboard` | `services.py`, `serializers.py` | Displayed on operational calendar: `event["title"] = booking.customer_name`, `event["customer_name"] = booking.customer_name`. |
| `apps/audit` | `services.py`, `serializers.py` | Captured in `before_data` and `after_data` audit payloads. |
| `tests/bookings` | `test_booking_api.py` | Hundreds of test assertions verify customer name/phone inputs and persistence. |

### 4.2 Migration vs. Snapshot Distinction

> [!IMPORTANT]
> **Permanent Historical Snapshot Rule:**
> `Booking.customer_name` and `Booking.customer_phone` must **never be deleted or replaced** by foreign keys. They must remain forever as historical snapshot fields.

#### Why Snapshots Must Remain:
1. **Contractual & Financial Truth:** A court reservation is a point-in-time legal/financial agreement. If a customer changes their legal name or nickname a year later, past receipts, audit logs, and transaction reconciliations must preserve the name captured when the slot was reserved.
2. **Decoupled Life Cycle:** If a club updates a player's `display_name` via `ClubPlayer`, existing completed or cancelled bookings must not mutate retroactively.

#### Phased Customer Identity Target:

```text
Existing:
Booking.customer_name  (CharField - Snapshot)
Booking.customer_phone (PhoneNumberField - Snapshot)

Phase A (Upcoming):
Booking.player_profile (Nullable FK -> PlayerProfile)
Booking.club_player    (Nullable FK -> ClubPlayer)
Booking.customer_name  (Preserved Snapshot)
Booking.customer_phone (Preserved Snapshot)

Phase B (Future):
Booking creation auto-populates player_profile & club_player,
and copies club_player.display_name & player_profile.phone_number
into the snapshot fields.
```

---

## 5. Booking Creation Flow

### 5.1 Step-by-Step Trace

```text
POST /api/v1/clubs/{club_slug}/bookings/
Payload: {
    "court": 12,
    "customer_name": "Ahmed Hassan",
    "customer_phone": "+201012345678",
    "start_time": "2026-05-20T18:00:00+02:00",
    "end_time": "2026-05-20T19:00:00+02:00",
    "client_request_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "is_recurring": false,
    "notes": "Bring size 5 ball"
}
```

1. **Serializer Validation (`BookingCreateSerializer.validate`):**
   - Resolves `court` instance.
   - Checks `court.club_id == access.club.id`.
   - Checks `court.is_active` and `court.club.is_active`.
   - Validates `access.can_create_booking_for_court(court)` (Staff court assignment).
   - Validates slot duration against `court.slot_duration_minutes`.
   - Validates `source == ADMIN_CORRECTION` requires superadmin.

2. **Service Execution (`create_booking` in `services.py`):**
   - Opens `transaction.atomic()`.
   - Locks the court using `select_for_update()`.
   - **Idempotency Gate:** If `client_request_id` is supplied:
     - Locks the club row to prevent concurrent race conditions on the same UUID.
     - Checks existing `BookingAttempt` and `Booking` for `client_request_id`.
     - If matching request exists: returns existing booking (HTTP 200).
     - If conflicting payload: raises `BOOKING_CLIENT_REQUEST_MISMATCH` (HTTP 409).
   - **Pricing Engine:** Calls `calculate_booking_price(locked_court, start_time, end_time)`.
     - Inspects court weekly working hours and pricing periods.
     - Sums base and surge period rates. Client input has zero influence over price.
   - **Availability Gate:** Calls `validate_no_availability_conflict()`.
     - Queries existing bookings on `locked_court` overlapping `[start_time, end_time)`.
     - Filters by `status__in=BLOCKING_STATUSES` (`HOLD`, `CONFIRMED`, `COMPLETED`, `NO_SHOW`).
     - Checks weekly working hours boundaries.
     - If conflict: creates `BookingAttempt(outcome=REJECTED, failure_code=...)` and raises `BOOKING_SLOT_UNAVAILABLE` (HTTP 409).
   - **Recurrence Validation (if `is_recurring=True`):**
     - Calls `validate_can_start_recurrence()`.
     - Ensures no existing virtual recurrence conflicts exist on the weekly arithmetic slot.
   - **Booking Creation:**
     - Creates `Booking` row with `status=Booking.Status.HOLD`.
     - Sets `total_price = calculated_price`.
     - Sets `created_by = request.user`.
   - **Attempt Logging:** Creates `BookingAttempt(outcome=SUCCESS, resolution=RESOLVED)`.
   - **Audit Trail:** Calls `record_audit_log(action=BOOKING_CREATED)`.

3. **Transaction Creation Status:**
   - **Zero transactions are created during `create_booking()`.**
   - A newly created booking starts in `HOLD`.
   - Payment occurs downstream via `POST /api/v1/clubs/{slug}/transactions/`.
   - The first payment on a `HOLD` booking transitions its status to `CONFIRMED`.

---

## 6. Booking Lifecycle Flow

| Lifecycle Action | HTTP Route | Allowed Initial Statuses | End Status | Authorization Requirement | Resource Scope | Service Responsibility |
|:---|:---|:---|:---|:---|:---|:---|
| **Create** | `POST /bookings/` | — | `HOLD` | Matrix: `create` | `Club + Court` | Duration, pricing, working hours, overlap check, idempotency. |
| **First Payment** | `POST /transactions/` | `HOLD` | `CONFIRMED` | Transactions Matrix: `create` | `Club + Court` | Managed by Transactions domain; transitions booking to `CONFIRMED`. |
| **Cancel** | `POST /bookings/{id}/cancel/` | `HOLD`, `CONFIRMED` | `CANCELLED` | Matrix: `cancel` | `Club + Court` | If Staff: requires `reason`. Calculates refund policy. Creates refund transaction if paid. Ends recurrence if active. |
| **Cancellation Preview** | `POST /bookings/{id}/cancellation-preview/` | `HOLD`, `CONFIRMED` | Read-only | Missing from Matrix | `Club + Court` | Calculates refund preview without mutating state. |
| **Complete** | `POST /bookings/{id}/complete/` | `CONFIRMED` | `COMPLETED` | Matrix: `complete` | `Club + Court` | **Requires `remaining_amount == 0`**. If `continue_recurring`: spawns next weekly occurrence atomically, marks current `RENEWED`. |
| **No-Show** | `POST /bookings/{id}/no-show/` | `CONFIRMED` | `NO_SHOW` | Matrix: `no_show` | `Club + Court` | Records `no_show_at` and `no_show_reason`. Ends recurrence if active. Slot remains blocking. |
| **Reschedule** | `POST /bookings/{id}/reschedule/` | `HOLD`, `CONFIRMED` | `HOLD` / `CONFIRMED` | Matrix: `reschedule` | `Club + Target Court` | Non-recurring only. Overlap check on new slot. Recalculates price (updates `total_price` only if higher). |
| **Expire** | `POST /bookings/{id}/expire/` | `HOLD` | `EXPIRED` | Matrix: `expire` | `Club + Court` | Validates hold is expired based on court policy or start time. Releases slot. |
| **End Recurrence** | `POST /bookings/{id}/end-recurrence/` | Active recurring `HOLD` / `CONFIRMED` | No status change | Matrix: `end_recurrence` | `Club + Court` | Sets `recurrence_status = ENDED`. Frees future virtual weekly slots. |
| **Recurrence Next Preview** | `GET /bookings/{id}/recurrence-next/` | Active recurring `CONFIRMED` | Read-only | Matrix: `recurrence_next` | `Club + Court` | Computes preview of next weekly date, price, and availability. |

---

## 7. Offline / PWA Impact

### 7.1 Offline Architecture
1. **Idempotency Keys:**
   Clients generate client-side UUIDs (`client_request_id`) attached to offline booking creation.
2. **Historical Bookings Allowed:**
   Court bookings with `start_time` prior to the current server timestamp are accepted when synced (representing offline matches that already took place).
3. **`BookingAttempt` Synchronization Ledger:**
   If a client tries to sync while offline slots are conflicting, a `BookingAttempt(outcome=REJECTED)` row is persisted. Staff can later review and dismiss rejected attempts.

### 7.2 Risk Assessment of Introducing `ClubPlayer` into Offline Flows

> [!WARNING]
> **Severe Offline Risk Identified:**
> If `POST /bookings/` is modified to require `club_player_id` or `player_profile_id` in the request body, offline operation will fail when staff registers a new walk-in player without an internet connection.

#### Resolution Strategy:
1. The API payload must **continue accepting `customer_name` and `customer_phone`**.
2. When the request syncs, the backend service will idempotently resolve or create the `PlayerProfile` and `ClubPlayer` server-side inside the transaction.
3. Idempotency comparison (`booking_matches_client_request`) must continue evaluating `customer_name` and `customer_phone` to ensure exact payload replay matching.

---

## 8. Authorization Spine Migration Plan

### Phase A — Identity Preparation (Recommended Next Sprint)

- **Goal:** Prepare database schema and serializers for `ClubPlayer` / `PlayerProfile` linking with **zero breaking changes** and **zero behavior changes**.
- **Model Additions to `Booking` (`apps/bookings/models.py`):**
  ```python
  player_profile = models.ForeignKey(
      "players.PlayerProfile",
      null=True,
      blank=True,
      on_delete=models.SET_NULL,
      related_name="bookings",
      help_text="Global customer profile link (nullable for legacy/soft rollout).",
  )
  club_player = models.ForeignKey(
      "players.ClubPlayer",
      null=True,
      blank=True,
      on_delete=models.SET_NULL,
      related_name="bookings",
      help_text="Club-specific player representation link.",
  )
  ```
- **Preservation:** `customer_name` and `customer_phone` remain untouched.
- **Service Integration:** In `create_booking()`, call `find_or_create_player_profile()` and `get_or_create_club_player()` to populate these foreign keys for new bookings.
- **Tests:** Add unit tests verifying FK population and snapshot preservation.

---

### Phase B — Authorization Migration (Spine v2)

- **Goal:** Move `BookingViewSet` and `BookingAttemptViewSet` from `ClubScopedAccessMixin` to `SlotyScopedResourceMixin` + `SlotyBasePermission`.

#### 1. Model `authorization_config` Declarations
On `Booking`:
```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
        "court": {"path": "court"},
    },
    "default_scope": "court",
    "select_related": (
        "club",
        "court",
        "created_by",
        "last_status_changed_by",
        "previous_recurring_booking",
        "next_recurring_booking",
    ),
    "prefetch_related": (),
}
```
On `BookingAttempt`:
```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
        "court": {"path": "court"},
    },
    "default_scope": "court",
    "select_related": ("club", "court", "attempted_by", "booking"),
    "prefetch_related": (),
}
```

#### 2. Matrix Reconciliation in `apps/common/authorization/matrix.py`
1. Add missing action `"cancellation_preview"` to `BookingViewSet` for `ADMIN`, `OWNER`, `MANAGER`, `STAFF`.
2. Reconcile `"update"` and `"partial_update"` permissions for `OWNER`, `MANAGER`, and `STAFF` to maintain parity with active tests and frontend editing.

#### 3. ViewSet Refactoring (`apps/bookings/views.py`)
- Inherit `SlotyScopedResourceMixin` instead of `ClubScopedAccessMixin`.
- Set `permission_classes = (SlotyBasePermission,)`.
- Set `authorization_model = Booking`, `authorization_scope = ResourceScope.COURT`.
- Replace `get_lifecycle_booking()`:
  ```python
  # Old (existence leak):
  get_object_or_404(Booking, pk=..., club=access.club)

  # New (fail-closed, court-scoped):
  self.get_object()  # Automatically uses scoped queryset
  ```
- Remove serializer permission check in `BookingCreateSerializer.validate()` (`access.can_create_booking_for_court`). The view layer and court queryset handle court validation.

#### 4. Service Clean-up (`apps/bookings/services.py`)
- Remove `validate_booking_for_lifecycle_action()` calls that check `access.can_change_booking_status()`. The spine's scoped queryset guarantees that any booking retrieved belongs to an authorized court.
- In `reschedule_booking()`, validate target court using `resolve_court_scope` or Spine helper rather than legacy `access.can_access_court(locked_court)`.

---

### Phase C — Legacy Removal

Once Bookings, Dashboard, and Reports are migrated:
- Deprecate and remove from `apps/clubs/access.py`:
  - `scoped_bookings_queryset()`
  - `scoped_booking_attempts_queryset()`
  - `can_create_booking_for_court()`
  - `can_change_booking_status()`
  - `can_access_booking_attempt()`
  - `can_dismiss_booking_attempt()`
- Remove `CanManageClubBookings` and `CanManageBookingAttempts`.

---

## 9. Genuine Domain Rules vs. Authorization Spine

To keep the architecture clean, domain logic must not duplicate the security spine:

| Rule Description | Belongs To | Rationale |
|:---|:---|:---|
| User is active member of club | **Authorization Spine** | Tenant resolution (`resolve_club_scope`). |
| Staff is assigned to court | **Authorization Spine** | Resource scoping (`ResourceScope.COURT`). |
| Staff cancellation requires a reason | **Domain Service** | Business condition evaluated in `cancel_booking()`. |
| Staff can only dismiss own rejected attempts | **Domain Permission / Service** | Creator-specific business restriction on attempts. |
| Hold booking cannot be completed | **Domain Service** | State machine invariant (`HOLD` requires payment first). |
| Completion requires remaining balance == 0 | **Domain Service** | Financial completion invariant. |
| Recurrence continuation creates next booking | **Domain Service** | Business workflow. |
| Cancellation refund calculation by notice period | **Domain Service** | Pricing/refund business policy. |
| Terminal statuses cannot transition | **Domain Service** | State machine invariant. |

---

## 10. QuerySet Security Review

| Query Location | Current Implementation | Security Assessment | Remediation in Phase B |
|:---|:---|:---|:---|
| `BookingViewSet.get_queryset()` | Calls `scoped_bookings_queryset()` via legacy `ClubAccessContext`. | Court-scoped for list, but uses legacy engine. | Switch to `SlotyScopedResourceMixin.get_scoped_queryset()`. |
| `BookingViewSet.get_lifecycle_booking()` | `Booking.objects.filter(pk=..., club=access.club)`. | **Vulnerability: Missing court filter.** Relies on service check; causes HTTP 403 existence leak. | Replace with standard `self.get_object()` using court-scoped queryset (returns HTTP 404). |
| `BookingViewSet.slots()` | Calls `generate_booking_slots()`. Court validated in service. | Functional, but court check is manual in service. | Validate court parameter using Spine context. |
| `BookingAttemptViewSet.get_queryset()` | Calls `scoped_booking_attempts_queryset()`. Filters by `court` and `attempted_by` if staff. | Correctly scoped, but uses legacy engine. | Move court scope to Spine; keep `attempted_by` filter in ViewSet `filter_scoped_queryset()`. |

---

## 11. Tests Review

### 11.1 Current Test Suite Coverage (`tests/bookings/`)
- `test_booking_api.py` (5,827 lines):
  - Creation, duration validation, working hour validation, pricing period splits.
  - Overlap conflicts and locking statuses.
  - All lifecycle actions (`cancel`, `complete`, `no_show`, `reschedule`, `expire`, `end_recurrence`).
  - Recurrence continuation, virtual slots, renewed status.
  - Idempotency, request replay, and mismatch handling.
  - Basic staff court isolation tests.
- `test_booking_attempt_model.py` (405 lines):
  - Attempt database constraints, outcome consistency, resolution states.
- `test_egypt_timezone_boundaries.py` (186 lines):
  - Africa/Cairo midnight boundary crossings, slot generation across midnight.

### 11.2 Required Pre-Migration / Regression Tests
Before executing Phase B, the following tests must be established:
1. **HTTP 404 Court Isolation Test:** Update `test_staff_cannot_change_booking_status_for_another_court_in_same_club` to verify whether HTTP 404 (Spine standard) is the approved target behavior.
2. **Matrix Action Tests:** Explicit unit tests verifying all 4 roles against each booking action via `SlotyBasePermission`.
3. **Spine Model Contract Tests:** Standalone tests verifying `load_authorization_config(Booking)` and `scoped_queryset(context, Booking)` independent of HTTP requests (pattern matching `tests/courts/test_court_authorization.py`).

---

## 12. Recommended Sprint Breakdown

```text
┌─────────────────────────────────────────────────────────────┐
│ SPRINT: Booking Migration Phase A (Identity Linkage)        │
├─────────────────────────────────────────────────────────────┤
│ 1. Add nullable player_profile and club_player FKs to      │
│    Booking model.                                           │
│ 2. Create schema migration.                                 │
│ 3. Update create_booking() to resolve/create profiles and   │
│    populate FKs while preserving customer_name / phone.     │
│ 4. Expose player_profile and club_player in read serializers│
│    (read-only).                                             │
│ 5. Verify all existing tests pass with zero regressions.    │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ SPRINT: Booking Migration Phase B (Authorization Spine v2)  │
├─────────────────────────────────────────────────────────────┤
│ 1. Declare authorization_config on Booking & BookingAttempt.│
│ 2. Update matrix.py: add cancellation_preview, reconcile    │
│    partial_update for Owner/Manager/Staff.                  │
│ 3. Switch BookingViewSet to SlotyScopedResourceMixin and     │
│    SlotyBasePermission.                                     │
│ 4. Refactor get_lifecycle_booking to use self.get_object().  │
│ 5. Clean court checks out of serializers and services.      │
│ 6. Add tests/bookings/test_booking_authorization.py.        │
│ 7. Run full regression suite across bookings, transactions, │
│    settlements, dashboard.                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 13. Sign-off & Next Steps

This audit confirms that `apps/bookings/` is architecturally ready for **Booking Migration Phase A**.

By decoupling **Identity Linkage (Phase A)** from **Authorization Spine Migration (Phase B)**, the team can introduce `PlayerProfile` and `ClubPlayer` safely without touching the delicate permission matrix or lifecycle authorization checks in the same release.

> [!NOTE]
> **Phase A implementation outcome (2026-09-13):** Phase A was completed with one refinement to §1.4, §4.2, and §8's proposed model — `Booking` received **only** the nullable `club_player` FK, not a separate `player_profile` FK. See [`ADR-002` §5 (Addendum)](adr/ADR-002-booking-identity-and-authorization-migration.md#5-addendum--phase-a-implementation-refinement-2026-09-13) for the rationale. `player_profile` / `club_player` were **not** exposed on read serializers in this phase (kept as an internal identity linkage only) — that remains open for Phase B to decide alongside the rest of the API surface migration. All other Phase A scope (schema migration, `create_booking()` resolution, snapshot preservation, zero authorization changes, regression tests) was completed as planned.
