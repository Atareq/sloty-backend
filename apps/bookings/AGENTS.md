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
  - `customer_name`, `customer_phone` (permanent snapshots — see below), `club_player` (resolved identity FK — see below), `start_time`, `end_time`, `total_price`.
  - `source`: `MANUAL`, `ADMIN_CORRECTION`, `RECURRING`.
  - `recurrence_status`: `ACTIVE`, `RENEWED`, `ENDED` (null for non-recurring).
  - `previous_recurring_booking`: Pointer to prior recurrence occurrence.
  - Traceability: `cancellation_reason`, `no_show_reason`, `reschedule_reason`, `completed_at`, `cancelled_at`, `no_show_at`, `expired_at`, `last_status_changed_by`.
- [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - Preserves requested parameters, `outcome` (`SUCCESS`/`REJECTED`), `failure_code`, `failure_details`, and `resolution` (`UNRESOLVED`, `DISMISSED`, `RESOLVED`).

## Customer Identity Architecture (Locked — Phase A + Sprint 2 Enforcement Complete)

Final identity model:

```text
Booking
  │
  ▼
ClubPlayer
  │
  ▼
PlayerProfile
```

**`Booking` belongs to a `ClubPlayer`. It does NOT hold a direct `PlayerProfile` FK.** `PlayerProfile` is only reachable transitively via `booking.club_player.player_profile`. This is a deliberate refinement of the original [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md) Decision 1 (which proposed two FKs) — see the ADR-002 addendum for the rationale: the business identity of a booking is "this club's representation of a player," not "a global person," and `ClubPlayer.player_profile` is immutable, so a second direct FK would only add redundancy without a distinct invariant to protect.

- `PlayerProfile` — global customer identity, keyed by phone number (`apps/players/`).
- `ClubPlayer` — club-local representation/naming of that person (`apps/players/`). **As of the ClubPlayer Historical Identity Foundation sprint, `ClubPlayer` is append-oriented and historical** — it is soft-deleted-and-replaced (`apps.players.services.supersede_club_player()`), never edited in place. `Booking.club_player_id` is therefore itself the historical identity snapshot as of when the booking was made: superseding a `ClubPlayer` never changes what any existing `Booking` points to or displays. See `apps/players/AGENTS.md` "ClubPlayer Lifecycle (Append-Only)" for the full model — this app only consumes it, it does not own the lifecycle.
- `Booking.club_player` — nullable `ForeignKey(players.ClubPlayer, on_delete=SET_NULL, related_name="bookings")`. Nullable because rows created directly via ORM/admin/fixtures (bypassing `create_booking()`) are not required to resolve identity; every booking created through the real API/service always has one populated.

### Why `customer_name` / `customer_phone` Remain (Documented Decision)

These fields were reviewed against actual usage (not preserved out of migration caution) and are **kept intentionally** because they are read by systems beyond player identification:

- `apps/audit/services.py` — `booking_audit_snapshot()` / `transaction_audit_snapshot()` embed them in every audit log `before_data`/`after_data` payload (append-only trail; must not retroactively change).
- `apps/dashboard/services.py` — the operational calendar event `title`/`customer_name`/`customer_phone` fields render directly from the booking snapshot, independent of any later `ClubPlayer.display_name` edit.
- `apps/transactions/serializers.py` — `booking_customer_name` / `booking_customer_phone` are denormalized onto transaction/receipt responses.
- `apps/bookings/filters.py` — `BookingFilter.filter_search` performs `icontains` / Egyptian-phone-variant search directly on these columns.
- `apps/bookings/services.py` — idempotency payload matching (`booking_matches_client_request`, `booking_attempt_matches_client_request`) compares the submitted snapshot values.

**Permanent invariant:** these fields must never mutate to reflect a later `ClubPlayer.display_name` change. A booking's receipt/audit trail reflects the name given *at the time it was made*, even if the club later renames that player.

### Identity Invariant: `Booking.club_id == Booking.club_player.club_id`

A booking from Club A must never reference a Club B `ClubPlayer`. Enforced at three levels:

1. **Model** — `Booking.clean()` raises `ValidationError` on `club_player` when `club_player.club_id != club_id`.
2. **Service** — `apps.bookings.services.validate_booking_club_player()` raises `SlotyAPIException(code="BOOKING_CLUB_PLAYER_MISMATCH")`. `resolve_booking_club_player()` (used by `create_booking()`) always calls this defensively even though it resolves `club_player` from the same locked club, protecting any future/alternate creation path.
3. **Database (PostgreSQL only)** — migration `0008_booking_club_player_and_more` adds a composite foreign key `Booking(club_player_id, club_id) → ClubPlayer(id, club_id)` (via `UNIQUE(id, club_id)` on `ClubPlayer` + a raw `ALTER TABLE ... FOREIGN KEY` in a guarded `RunPython` step). SQLite (local dev/test) cannot add FK constraints to an existing table without a full rebuild, so this step is a no-op there — the model + service checks are the enforcement layer for SQLite.

### Player Resolution Flow (Backend-Owned, Frontend-Opaque)

The frontend **never sends** `club_player_id` or `player_profile_id` for normal booking creation — only `customer_name` / `customer_phone`, exactly as before this sprint. Zero request/response contract changes.

```text
create_booking(customer_name, customer_phone, ...)
        │
        ▼
find_or_create_player_profile(phone) ──► PlayerProfile (global, by phone)
        │
        ▼
get_or_create_club_player(club, player_profile) ──► ClubPlayer (this club's row)
        │
        ▼
validate_booking_club_player(club, club_player)   # defensive invariant check
        │
        ▼
Booking.objects.create(club_player=club_player, customer_name=..., customer_phone=..., ...)
```

This preserves offline/PWA walk-in booking: staff can register a brand-new phone number with zero network dependency on a pre-existing player record.

**ClubPlayer resolution rules (Sprint 2 — Booking Identity Enforcement):** `get_or_create_club_player()` (owned by `apps/players/`, see its "ClubPlayer Lifecycle (Append-Only)" section) only ever matches or creates an **active** (`deleted_at IS NULL`) `ClubPlayer` row. A soft-deleted `ClubPlayer` is never returned or resurrected — if the previous active row for a `(club, player_profile)` pair was superseded, `create_booking()` transparently resolves/creates a fresh active one. This app does not implement any of that logic itself; it only consumes `get_or_create_club_player()` and never bypasses it with a direct lookup.

**Direct/internal creation paths (audited, Sprint 2):** every real business booking-creation path goes through `create_booking()` above. The only other production code path that creates `Booking` rows directly is `apps.accounts.management.commands.seed_demo_data` (dev/demo fixture data via `Booking.objects.update_or_create()`), which now also calls `resolve_booking_club_player()` before writing so seeded bookings carry a real `club_player` like any API-created one. `complete_booking()`'s recurrence continuation (`Booking.objects.create()`) is the one legitimate internal path that does not call `resolve_booking_club_player()` — see "Booking-Native Recurrence" above for why it copies `club_player` directly instead. All other `Booking.objects.create()` usages in the codebase are test fixtures (`tests/`, `apps/reports/tests/`) and are out of scope for this invariant — nullable `club_player` on ORM-created rows outside the service layer is expected and accounted for in `Booking.club_player`'s field documentation.

- **Booking Phase A (Complete):** `club_player` FK added; `create_booking()` auto-resolves identity; `customer_name`/`customer_phone` preserved forever as snapshots. **No authorization/permission/ViewSet changes were made in this phase.**
- **Booking Phase B (Future, separate sprint):** Migrate `BookingViewSet` / `BookingAttemptViewSet` to Authorization Spine v2 (`SlotyScopedResourceMixin` + `SlotyBasePermission` + `authorization_config`). Do not conflate this with Phase A.
- **Resource Scopes (target, not yet migrated):**
  - `Booking`: **Club + Court** (Staff: assigned courts; **not** creator-restricted).
  - `BookingAttempt`: **Club + Court + attempted_by (for Staff)**.
- See [`booking-migration-audit-v1.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/booking-migration-audit-v1.md) and [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md).

## Service Layer

- [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py):
  - `create_booking()`: Enforces court working hours, slot alignment, overlap check, price calculation, idempotency, and identity resolution (`resolve_booking_club_player()`).
  - `resolve_booking_club_player()` / `validate_booking_club_player()`: Own the `Booking -> ClubPlayer -> PlayerProfile` identity resolution and the club-isolation invariant. See "Customer Identity Architecture" above.
  - `cancel_booking()`: Validates notice period, calculates refunds, creates refund transactions, updates recurrence.
  - `complete_booking()`: Validates zero remaining balance, handles recurrence continuation. When continuing, copies `club_player` from the anchor onto the new occurrence (see "Booking-Native Recurrence" above) rather than re-resolving identity.
  - `reschedule_booking()`: Overlap check excluding current booking, updates price if higher.
  - `no_show_booking()`, `expire_booking()`, `expire_due_hold_bookings()`.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/bookings/`: List, create, retrieve, partial update (customer info/notes only on non-locked bookings).
- Lifecycle action routes:
  - `POST .../bookings/{id}/cancel/`
  - `POST .../bookings/{id}/complete/`
  - `POST .../bookings/{id}/no-show/`
  - `POST .../bookings/{id}/reschedule/`
  - `POST .../bookings/{id}/expire/`
  - `POST .../bookings/{id}/end-recurrence/`
  - `GET .../bookings/{id}/recurrence-next/`
- `/api/v1/clubs/{club_slug}/bookings/slots/`: Schedule slot availability generator.
- `/api/v1/clubs/{club_slug}/booking-attempts/`: Traceability list/detail and dismissal for rejected attempts.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization. Target: Authorization Spine v2 with `authorization_config` `default_scope="court"` (root AGENTS.md §5). Do **not** create `bookings/authorization.py` solely to re-express court assignment — the spine owns that. Domain `authorization.py` is allowed only for true business invariants the matrix cannot express.

- Scoped via `ClubAccessContext`.
- Staff users can list, create, and manage bookings and attempts only for their assigned court.
- Staff can dismiss only their own unresolved rejected booking attempts.

## Cross-App Dependencies

- Calls `apps.courts.pricing` for price snapshots and working-hour boundary checks.
- Interacts with `apps.transactions` for payment status, cancellation refund rows, and remaining balance checks.
- Generates audit logs via `apps.audit.services`.
- Connects to `apps.players` (`find_or_create_player_profile()`, `get_or_create_club_player()`) for customer identity resolution — `Booking.club_player` FK (see [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md) and [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)).

## Testing

- Test suite: [`tests/bookings/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/bookings/).
- Key test files:
  - `test_booking_api.py`: Creation, validation, lifecycle endpoints, recurrence.
  - `test_booking_attempt_model.py`: Idempotency, attempt persistence, and dismissal.
  - `test_egypt_timezone_boundaries.py`: Local Cairo timezone and midnight handling.
  - `test_booking_player_identity.py`: `Booking -> ClubPlayer -> PlayerProfile` resolution — same-phone/same-club reuse, same-phone/different-clubs isolation, cross-club rejection (model + service), creation-payload compatibility (no player IDs required); (Sprint 1) that `Booking.club_player_id` survives `supersede_club_player()` unchanged while a subsequent new booking resolves the new active `ClubPlayer`; and (Sprint 2) that `create_booking()` never reuses a soft-deleted `ClubPlayer` and that `complete_booking(continue_recurring=True)` propagates `club_player` to the next occurrence.
