# Bookings — Agent Guide

## Responsibility

`apps/bookings/` owns booking creation, lifecycle state transitions, agreed-price snapshots, slot availability and overlap protection, booking-native recurrence, hold expiry logic, and offline attempt history.

## Domain Invariants

- **Price Snapshot Authority**:
  - `Booking.total_price` is calculated by the backend from court pricing periods at creation time. Clients must never control booking price. Price does not change when court working-hour pricing changes later.
- **Booking Statuses & Overlap Protection**:
  - Statuses: `HOLD`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`.
  - **Blocking Statuses**: `HOLD`, `CONFIRMED`, `COMPLETED`, `NO_SHOW`. These block overlapping reservations on the same court.
  - **Non-blocking Statuses**: `CANCELLED` and `EXPIRED` release their time slot.
  - Overlap collision returns `BOOKING_SLOT_UNAVAILABLE`.
  - `FREE` and `UNAVAILABLE` are display-level availability response states only and must never be added to `Booking.Status`.
- **Service-Controlled Lifecycle**:
  - All status transitions must execute through [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py) inside `transaction.atomic()` with `select_for_update()`.
  - Allowed transitions:
    - `HOLD -> CANCELLED`, `HOLD -> EXPIRED`, `HOLD -> CONFIRMED` (via first transaction).
    - `CONFIRMED -> CANCELLED`, `CONFIRMED -> COMPLETED`, `CONFIRMED -> NO_SHOW`, `CONFIRMED -> HOLD` (via transaction cancellation).
  - Terminal statuses (`COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`) cannot transition further.
- **Completion Invariant**:
  - A booking cannot transition to `COMPLETED` while any dynamic remaining amount exists (`remaining_amount > 0`).
  - Violation raises `BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT` (409 Conflict).
  - The backend must never auto-create cash transactions upon completion.
- **Hold Expiry Calculation**:
  - Single authoritative rule:
    ```text
    effective_hold_expires_at = min(
        booking.created + court.internal_hold_expiry_hours,
        booking.start_time
    )
    ```
  - Executed via `python manage.py expire_hold_bookings`. A HOLD whose start time has arrived is expired even if policy hours have not elapsed.
- **Reschedule Rules**:
  - Allowed only from `HOLD` or `CONFIRMED`.
  - Existing transactions remain attached.
  - If recalculated price is higher, update `total_price`; if lower or equal, retain existing `total_price`.
- **Booking-Native Recurrence**:
  - Recurrence is booking-native (`source=RECURRING`, `recurrence_status` in `ACTIVE`, `RENEWED`, `ENDED`). Do not create a separate recurring app or series model.
  - Future recurring slots are virtual arithmetic matches (`slot_status=RECURRING_RESERVED`).
  - Completion with `continue_recurring=true` creates the next weekly occurrence atomically and marks the anchor `RENEWED`.
  - **Identity propagation (Sprint 2 — Booking Identity Enforcement):** the next occurrence created by `complete_booking()` copies `club_player` directly from the anchor booking — it is the *same customer's continuation of the same series*, not a new identity interaction, so it is never re-resolved via `resolve_booking_club_player()`. `previous_recurring_booking` and `club_player` therefore always agree on identity across an entire recurrence chain.
  - Cancelling, no-showing, or expiring an active recurrence sets `recurrence_status=ENDED`. Active recurring bookings cannot be rescheduled.
- **Offline / PWA Sync & Idempotency**:
  - `client_request_id` is an optional UUID idempotency key scoped to the Club. Retries with the same key and payload return HTTP 200; mismatched payloads return `BOOKING_CLIENT_REQUEST_MISMATCH` (409 Conflict).
  - Historical bookings are permitted: an appointment time being before server sync time is not a rejection reason.
  - Persistent [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py) records track intent and outcomes. Rejected attempts never affect operational bookings or availability.

## Important Models & Fields

- [`Booking`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - `club_player` (required FK to the exact ClubPlayer version used at booking time), `start_time`, `end_time`, `total_price`.
  - There is no `Booking.customer_name` / `Booking.customer_phone` column and no `Booking.player_profile` FK.
  - `source`: `MANUAL`, `ADMIN_CORRECTION`, `RECURRING`.
  - `recurrence_status`: `ACTIVE`, `RENEWED`, `ENDED` (null for non-recurring).
  - `previous_recurring_booking`: Pointer to prior recurrence occurrence.
  - Traceability: `cancellation_reason`, `no_show_reason`, `reschedule_reason`, `completed_at`, `cancelled_at`, `no_show_at`, `expired_at`, `last_status_changed_by`.
- [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - Preserves requested parameters, `outcome` (`SUCCESS`/`REJECTED`), `failure_code`, `failure_details`, and `resolution` (`UNRESOLVED`, `DISMISSED`, `RESOLVED`).

## Customer Identity Architecture (Locked — Sprint 3 Integration Complete)

Final identity model:

```text
Phone Number
      │
      ▼
PlayerProfile
      │
      ▼
ClubPlayer versions  (is_current_version, previous_version, created_at)
      │
      ▼
Booking.club_player_id   ← exact version used at creation time
                           also the source of last-used recency
```

**`Booking` belongs to a `ClubPlayer`. It does NOT hold a direct `PlayerProfile` FK.**

- `PlayerProfile` — global person, keyed by phone. Different phone = different profile. No merging.
- `ClubPlayer` — immutable club-local version. No soft delete. No `last_used_at`. No `updated_at`. `create_club_player_version()` marks the old row historical and inserts a new current row. See `apps/players/AGENTS.md`.
- `Booking.club_player` — required FK (`on_delete=PROTECT`). Default resolution uses the **current** version. Optional write-only `club_player_id` on create selects a historical version (same club + same phone's profile). Recurring continuation copies the anchor version.
- **Last-used version** — `club_player` on the latest Booking for this `player_profile_id` at this club (`Booking.created` desc). Used for search recommendation. Never written back onto ClubPlayer.

### Why Booking snapshot columns are gone

`ClubPlayer` versions are the historical identity. `Booking.club_player_id` points at the exact version used at booking time, so denormalized `customer_name` / `customer_phone` columns on Booking were redundant operational copies.

Shared helper: [`apps/bookings/identity.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/identity.py) (`booking_customer_display_name`, `booking_customer_display_phone`, `booking_identity_search_q`). There is no snapshot fallback.

**Final consumer map:**

| Consumer | Category | Final state |
|---|---|---|
| Booking list/detail/slots/calendar | C API keys, operational display | Reads ClubPlayer; response keys stay `customer_name` / `customer_phone` |
| Create API | B walk-in input | Still accepts `customer_name` / `customer_phone`; optional `club_player_id` |
| PATCH | notes only | Does **not** rename ClubPlayer identity |
| `apps/dashboard/services.py` calendar | operational display | ClubPlayer only |
| `apps/transactions` / `apps/settlements` | operational display | ClubPlayer; keys stay `booking_customer_*` |
| Search / admin | operational search | ClubPlayer display name + profile phone |
| `apps/audit/services.py` snapshots | D historical event facts | New events store ClubPlayer display at event time; historical JSON is not rewritten |
| BookingAttempt `customer_*` | E request payload evidence | **Kept** — submitted payload, not Booking identity |
| Idempotency | F replay | Attempt payload (name/phone) plus Booking court/time/source/notes/phone/`club_player_id` |
| Recurrence | copies `club_player` only | Same version as the anchor |
| Reports court-usage | no customer identity in output | Unchanged |

Walk-in create still does **not** require frontend `club_player_id` / `player_profile_id`.

Booking authorization uses the Authorization Spine.

### Identity Invariant: `Booking.club_id == Booking.club_player.club_id`
A booking from Club A must never reference a Club B `ClubPlayer`. Enforced at three levels:

1. **Model** — `Booking.clean()` raises `ValidationError` on `club_player` when `club_player.club_id != club_id`.
2. **Service** — `apps.bookings.services.validate_booking_club_player()` raises `SlotyAPIException(code="BOOKING_CLUB_PLAYER_MISMATCH")`. `resolve_booking_club_player()` (used by `create_booking()`) always calls this defensively even though it resolves `club_player` from the same locked club, protecting any future/alternate creation path.
3. **Database (PostgreSQL only)** — migration `0008_booking_club_player_and_more` adds a composite foreign key `Booking(club_player_id, club_id) → ClubPlayer(id, club_id)` (via `UNIQUE(id, club_id)` on `ClubPlayer` + a raw `ALTER TABLE ... FOREIGN KEY` in a guarded `RunPython` step). SQLite (local dev/test) cannot add FK constraints to an existing table without a full rebuild, so this step is a no-op there — the model + service checks are the enforcement layer for SQLite.

### Player Resolution Flow (Backend-Owned, Frontend-Opaque)

Normal booking creation still sends only `customer_name` / `customer_phone`. Optional write-only `club_player_id` selects a historical version.

```text
create_booking(customer_name, customer_phone, club_player_id=optional)
        │
        ▼
find_or_create_player_profile(phone) ──► PlayerProfile (global, by phone)
        │
        ▼
if club_player_id:
    use that ClubPlayer version (must match club + this profile)
else:
    get_or_create_club_player(...) ──► current ClubPlayer version
        │
        ▼
Booking.objects.create(club_player=..., ...)
```

Walk-in phones still create a first current version. A later booking with the same phone uses the current version even if `customer_name` differs; name changes are `create_club_player_version()` / `POST /players/`, not booking create.

`complete_booking()` recurrence copies the anchor `club_player`. Seed data uses `resolve_booking_club_player()`. Create API still accepts walk-in `customer_name` / `customer_phone` as resolution inputs only.

## Authorization & Scoping (Authorization Spine v2)

Booking and BookingAttempt ViewSets compose `SlotyScopedResourceMixin` + `SlotyBasePermission`. Models declare `authorization_config` (`default_scope="court"`). The secured queryset is built before filters, pagination, and serializers.

```text
Request → Authentication → resolve_club_scope → Spine scoped QuerySet (club + court)
  → django-filter → ViewSet / Service (state machine, pricing, overlap)
```

- **Booking boundary:** Club + Court. Not `created_by`. Staff on Court A see all bookings on Court A.
- **Out-of-scope rows:** omitted from the queryset → HTTP 404 on retrieve and lifecycle actions (no existence leak via 403).
- **Create / slots / reschedule target court:** the court in the request body must be inside the same Spine court queryset; denial remains HTTP 403.
- **BookingAttempt:** Club + Court, then Staff narrowed to `attempted_by=request.user`. Owner/Admin can list/retrieve club+court attempts but may dismiss only their own (`check_object_permission`).
- Do **not** create `bookings/authorization.py` to re-express court assignment. `actor_requires_staff_cancel_reason()` remains a service business rule (Staff cancel requires a reason).
- Booking ViewSets consume the Profile-backed Authorization Spine. The compatibility-only `ClubAccessContext` is not used by runtime request handling.

## Service Layer

- [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py):
  - `create_booking()`: Enforces court working hours, slot alignment, overlap check, price calculation, idempotency, and identity resolution (`resolve_booking_club_player()`).
  - `resolve_booking_club_player()` / `validate_booking_club_player()`: Own the `Booking -> ClubPlayer -> PlayerProfile` identity resolution and the club-isolation invariant. See "Customer Identity Architecture" above.
  - `cancel_booking()`: Validates notice period, calculates refunds, creates refund transactions, updates recurrence.
  - `complete_booking()`: Validates zero remaining balance, handles recurrence continuation. When continuing, copies `club_player` from the anchor onto the new occurrence (see "Booking-Native Recurrence" above) rather than re-resolving identity.
  - `reschedule_booking()`: Overlap check excluding current booking, updates price if higher.
  - `no_show_booking()`, `expire_booking()`, `expire_due_hold_bookings()`.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/bookings/`: List, create, retrieve, partial update (**notes** only on non-locked bookings). Identity changes are ClubPlayer versioning, not Booking PATCH.
- Lifecycle action routes:
  - `POST .../bookings/{id}/cancel/`
  - `POST .../bookings/{id}/complete/`
  - `POST .../bookings/{id}/no-show/`
  - `POST .../bookings/{id}/reschedule/`
  - `POST .../bookings/{id}/expire/`
  - `POST .../bookings/{id}/end-recurrence/`
  - `POST .../bookings/{id}/cancellation-preview/`
  - `GET .../bookings/{id}/recurrence-next/`
- `/api/v1/clubs/{club_slug}/bookings/slots/`: Schedule slot availability generator.
- `/api/v1/clubs/{club_slug}/booking-attempts/`: Traceability list/detail and dismissal for rejected attempts.

## Cross-App Dependencies

- Calls `apps.courts.pricing` for price snapshots and working-hour boundary checks.
- Interacts with `apps.transactions` for payment status, cancellation refund rows, and remaining balance checks.
- Generates audit logs via `apps.audit.services`.
- Connects to `apps.players` (`find_or_create_player_profile()`, `get_or_create_club_player()`) for customer identity resolution — `Booking.club_player` FK (see [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md) and [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)).

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/bookings/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/bookings/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/bookings/).
- Key test files:
  - `test_booking_api.py`: Creation, validation, lifecycle endpoints, recurrence.
  - `test_booking_authorization.py`: Spine v2 contract, club/court isolation, 404 queryset boundary, creator independence, BookingAttempt `attempted_by`.
  - `test_booking_attempt_model.py`: Idempotency, attempt persistence, and dismissal.
  - `test_egypt_timezone_boundaries.py`: Local Cairo timezone and midnight handling.
  - `test_booking_player_identity.py`: identity chain, required `club_player`, version history, recurrence copy, walk-in create, PATCH does not mutate ClubPlayer, idempotency.
