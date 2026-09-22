# Bookings — Agent Guide

## Responsibility

`apps/bookings/` owns booking creation, lifecycle state transitions, agreed-price snapshots, slot availability and overlap protection, booking-native recurrence, hold expiry logic, and offline attempt history.

## Domain Invariants

- **Price Snapshot Authority**:
  - `Booking.total_price` is calculated by the backend from court pricing periods at creation time. Clients must never control booking price. Price does not change when court working-hour pricing changes later.
- **Booking Statuses & Overlap Protection**:
  - Statuses: `HOLD`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`.
  - **Blocking Statuses**: `HOLD`, `CONFIRMED`, `COMPLETED`. These block overlapping reservations on the same court.
  - **Non-blocking Statuses**: `CANCELLED`, `EXPIRED`, and `NO_SHOW` release their time slot.
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
- **Hold Expiry Snapshot**:
  - A HOLD stores `hold_expires_at` when it is created: creation time plus the
    court's configured `internal_hold_expiry_hours`. If a confirmed booking
    returns to HOLD after payment cancellation, a fresh snapshot starts then.
  - Court setting changes do not recalculate stored deadlines. A HOLD blocks
    overlap checks, slot generation, calendar, utilization, and court-usage
    occupancy only while `hold_expires_at > now`. Booking start time does not
    shorten the snapshot.
  - `python manage.py expire_hold_bookings` and
    `sloty-hold-expiry.timer` clean rows where
    `status=HOLD AND hold_expires_at <= now`; cleanup is not needed for
    availability correctness. Reads do not mutate booking status.
- **Reschedule Rules**:
  - Allowed only from `HOLD` or `CONFIRMED`.
  - Existing transactions remain attached.
  - If recalculated price is higher, update `total_price`; if lower or equal, retain existing `total_price`.
- **Booking-Native Recurrence**:
  - Recurrence is booking-native (`source=RECURRING`, `recurrence_status` in `ACTIVE`, `RENEWED`, `ENDED`). Do not create a separate recurring app or series model.
  - Future recurring slots are virtual arithmetic matches (`slot_status=RECURRING_RESERVED`).
  - Completion with `continue_recurring=true` creates the next weekly occurrence atomically and marks the anchor `RENEWED`.
  - **Identity propagation:** the next occurrence created by `complete_booking()` copies `club_player` directly from the anchor booking — it is the *same customer's continuation of the same series*, not a new identity interaction, so it is never re-resolved via `resolve_booking_club_player()`. `previous_recurring_booking` and `club_player` therefore always agree on identity across an entire recurrence chain.
  - Cancelling, no-showing, or expiring an active recurrence sets `recurrence_status=ENDED`. Active recurring bookings cannot be rescheduled.
- **Offline / PWA Sync & Idempotency**:
  - `client_request_id` is an optional UUID idempotency key scoped to the Club. Retries with the same key and payload return HTTP 200; mismatched payloads return `BOOKING_CLIENT_REQUEST_MISMATCH` (409 Conflict).
  - Historical bookings are permitted: an appointment time being before server sync time is not a rejection reason.
  - Persistent [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py) records track intent and outcomes. Rejected attempts never affect operational bookings or availability.

## Important Models & Fields

- [`Booking`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - `club_player` (required FK to the exact ClubPlayer version used at booking time), `start_time`, `end_time`, `total_price`.
  - There is no `Booking.customer_name` / `Booking.customer_phone` column and no `Booking.player_profile` FK.
  - `source`: `MANUAL` or `RECURRING`. Creator and lifecycle actor fields plus
    the audit log identify who performed an action; there is no
    `ADMIN_CORRECTION` source.
  - `recurrence_status`: `ACTIVE`, `RENEWED`, `ENDED` (null for non-recurring).
  - `previous_recurring_booking`: Pointer to prior recurrence occurrence.
  - `hold_expires_at`: Aware timestamp snapshot for HOLD expiry; null for
    non-HOLD rows. The field migration snapshots current court policy for
    preexisting HOLD rows.
  - Traceability: `termination_reason`, `completed_at`, `cancelled_at`,
    `no_show_at`, `expired_at`, `created_by` (`Profile`), and
    `last_status_changed_by` (`Profile`). `termination_reason` holds the latest
    cancellation, no-show, or reschedule reason; earlier lifecycle events remain
    in the append-only audit log.
  - Mutable timestamps come from `SlotyTimeStampedModel` (`created`, `modified`).
- [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - Append-only intent record (`SlotyCreatedModel`; `created` only, no `modified`).
  - Preserves requested parameters, `attempted_by` (`Profile`), `outcome` (`SUCCESS`/`REJECTED`), `failure_code`, `failure_details`, and `resolution` (`UNRESOLVED`, `DISMISSED`, `RESOLVED`).

- **Booking Retrieval and Scheduling**:
  - List and attempt `date_from`/`date_to` honor the requested range. A
    `YYYY-MM-DD` value is a local Cairo calendar day (`date_to` inclusive).
    ISO datetimes stay exact instants. The backend does not clamp the range
    to now or a fixed future window.
  - Slot generation remains in the application service. It retrieves working
    hours and bookings overlapping the requested aware datetime range, then
    decides slot blocking and pricing in Python.
  - On the current local day, a slot whose `start_time` is already in the
    past has `available=false` / `is_available=false`. Past calendar days
    stay bookable so historical create can still use FREE slots.
  - Keep `Booking(court, start_time)` for court range retrieval. Do not add
    hold-expiry or scheduling indexes without measured query evidence.

Booking `created_by` / `last_status_changed_by` and attempt `attempted_by`
response values are Profile IDs. The `attempted_by` filter also takes a Profile
ID; display names use the linked account full name or phone number.

## Customer Identity Architecture

### Core Principle: SIMPLICITY WINS
- ClubPlayer identity is `club + player_profile + exact display_name`.
- No version chains, stored version pointers, or current-version flags.
- Historical bookings keep the ClubPlayer they used.

Identity model:

```text
Phone Number
      │
      ▼
PlayerProfile (global, unique normalized phone)
      │
      ▼
ClubPlayer (club + player_profile + exact display_name)
      │
      ▼
Booking.club_player   ← exact ClubPlayer used at booking time
```

**`Booking` belongs to a `ClubPlayer`. It does NOT hold a direct `PlayerProfile` FK.**

- `PlayerProfile` — global person, keyed by phone. Different phone = different profile. No merging.
- `ClubPlayer` — club-local exact-name identity. No version chain. No soft delete. See `apps/players/AGENTS.md`.
- `Booking.club_player` — required FK (`on_delete=PROTECT`). Walk-in create resolves by phone then exact `display_name`. Optional write-only `club_player_id` on create selects a specific existing row (same club; phone/name must match when supplied). Recurring continuation copies the anchor's `club_player`.
- **Last-used** — `club_player` on the latest Booking for this `player_profile_id` at this club (`Booking.created` desc). Used for search recommendation. Recency, not identity equality.

### Why Booking snapshot columns are gone

`ClubPlayer` rows represent club-local customer identity. `Booking.club_player_id` points at the exact row used at booking time, so denormalized `customer_name` / `customer_phone` columns on Booking were redundant operational copies.

Shared helper: [`apps/bookings/identity.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/identity.py) (`booking_customer_display_name`, `booking_customer_display_phone`, `booking_identity_search_q`). There is no snapshot fallback.

**Final consumer map:**

| Consumer | Category | Final state |
|---|---|---|
| Booking list/detail/slots/calendar | C API keys, operational display | Reads ClubPlayer; response keys stay `customer_name` / `customer_phone` |
| Create API | B walk-in input | Still accepts `customer_name` / `customer_phone`; optional `club_player_id` |
| PATCH | notes, `customer_name`, and `customer_phone` | Resolves and assigns a new or existing ClubPlayer; never renames an existing ClubPlayer |
| `apps/dashboard/services.py` calendar | operational display | ClubPlayer only |
| `apps/transactions` / `apps/settlements` | operational display | ClubPlayer; keys stay `booking_customer_*` |
| Search / admin | operational search | ClubPlayer display name + profile phone |
| `apps/audit/services.py` snapshots | D historical event facts | New events store ClubPlayer display at event time; historical JSON is not rewritten |
| BookingAttempt `customer_*` | E request payload evidence | **Kept** — submitted payload, not Booking identity |
| Idempotency | F replay | Attempt payload (name/phone) plus Booking court/time/source/notes/phone/`club_player_id` |
| Recurrence | copies `club_player` only | Same ClubPlayer as the anchor |
| Reports court-usage | no customer identity in output | Unchanged |

Walk-in create still does **not** require frontend `club_player_id` / `player_profile_id`.

Booking authorization uses the Authorization Spine.

### Identity Invariant: `Booking.club_id == Booking.club_player.club_id`
A booking from Club A must never reference a Club B `ClubPlayer`. Enforced at three levels:

1. **Model** — `Booking.clean()` raises `ValidationError` on `club_player` when `club_player.club_id != club_id`.
2. **Service** — `apps.bookings.services.validate_booking_club_player()` raises `SlotyAPIException(code="BOOKING_CLUB_PLAYER_MISMATCH")`. `resolve_booking_club_player()` (used by `create_booking()`) always calls this defensively even though it resolves `club_player` from the same locked club, protecting any future/alternate creation path.
3. **Application enforcement** — `Booking.clean()` and `validate_booking_club_player()` remain the schema/service guards. The clean migration baseline does not declare a composite database FK; do not recreate the retired `0008` `RunPython` DDL.

### Player Resolution Flow (Backend-Owned, Frontend-Opaque)

Normal booking creation still sends only `customer_name` / `customer_phone`. Optional write-only `club_player_id` selects a specific existing row.

```text
create_booking(customer_name, customer_phone, club_player_id=optional)
        │
        ▼
find_or_create_player_profile(phone) ──► PlayerProfile (global, by phone)
        │
        ▼
if club_player_id:
    use that ClubPlayer (must match club; phone/name when supplied)
else:
    resolve_club_player(club, customer_name, customer_phone) ──► ClubPlayer
        │
        ▼
Booking.objects.create(club_player=..., ...)
```

Walk-in phones resolve `PlayerProfile` by phone and `ClubPlayer` by exact `display_name`. A later booking with the same phone and a different exact name creates a new ClubPlayer.

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
- **BookingAttempt:** Club + Court, then Staff narrowed to `attempted_by=context.profile`. Owner/Admin can list/retrieve club+court attempts but may dismiss only their own (`check_object_permission`).
- Do **not** create `bookings/authorization.py` to re-express court assignment. `actor_requires_staff_cancel_reason()` remains a service business rule (Staff cancel requires a reason).
- Booking ViewSets consume the Profile-backed Authorization Spine.

## Service Layer

- [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py):
  - `create_booking()`: Enforces court working hours, slot alignment, overlap check, price calculation, idempotency, and identity resolution (`resolve_booking_club_player()`).
  - `resolve_booking_club_player()` / `validate_booking_club_player()`: Own the `Booking -> ClubPlayer -> PlayerProfile` identity resolution and the club-isolation invariant. See "Customer Identity Architecture" above.
  - `cancel_booking()`: Validates notice period, calculates refunds, creates refund transactions, updates recurrence.
  - `complete_booking()`: Validates zero remaining balance, handles recurrence continuation. When continuing, copies `club_player` from the anchor onto the new occurrence (see "Booking-Native Recurrence" above) rather than re-resolving identity.
  - `reschedule_booking()`: Overlap check excluding current booking, updates price if higher.
  - `no_show_booking()`, `expire_booking()`, `expire_due_hold_bookings()`.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/bookings/`: List, create, retrieve, partial update (`notes`, `customer_name`, and `customer_phone` on non-locked bookings). When either identity field is supplied, the booking service resolves the ClubPlayer through `resolve_club_player()` using the submitted values plus any omitted current value; existing ClubPlayer rows are never renamed. List `date_from`/`date_to` accept `YYYY-MM-DD` (inclusive local days) or ISO datetimes and are not clamped to now.
- Lifecycle action routes:
  - `POST .../bookings/{id}/cancel/`
  - `POST .../bookings/{id}/complete/`
  - `POST .../bookings/{id}/no-show/`
  - `POST .../bookings/{id}/reschedule/`
  - `POST .../bookings/{id}/expire/`
  - `POST .../bookings/{id}/end-recurrence/`
  - `POST .../bookings/{id}/cancellation-preview/`
  - `GET .../bookings/{id}/recurrence-next/`
- `/api/v1/clubs/{club_slug}/bookings/slots/`: Schedule slot availability generator. Accepts `court` plus either `date` or inclusive `date_from`/`date_to`. The inclusive calendar-day span must be `<= MAX_SLOT_PERIOD_DAYS` (45) or the API returns `SLOT_PERIOD_TOO_LARGE` (400). Elapsed slots on the current local day are returned with `available=false`; historical days remain FREE for historical create.
- `/api/v1/clubs/{club_slug}/booking-attempts/`: Traceability list/detail and dismissal for rejected attempts. Attempt `date_from`/`date_to` use the same inclusive calendar-day / exact-datetime contract as booking list.

The frontend scheduling contract, including aware datetime handling, HOLD
deadlines, slot availability, and working-hours behavior, is documented in
[`docs/scheduling-contract.md`](../../docs/scheduling-contract.md). The current
OpenAPI schema is served at `/api/v1/schema/`.

## Cross-App Dependencies

- Calls `apps.courts.pricing` for price snapshots and working-hour boundary checks.
- Interacts with `apps.transactions` for payment status, cancellation refund rows, and remaining balance checks.
- Generates audit logs via `apps.audit.services`.
- Connects to `apps.players` (`find_or_create_player_profile`) and `apps.bookings.identity.resolve_club_player()` for customer identity — `Booking.club_player` FK (see [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md) and [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)).

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/bookings/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/bookings/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/bookings/).
- Key test files:
  - `test_booking_api.py`: Creation, validation, lifecycle endpoints, recurrence.
  - `test_booking_authorization.py`: Spine v2 contract, club/court isolation, 404 queryset boundary, creator independence, BookingAttempt `attempted_by`.
  - `test_booking_attempt_model.py`: Idempotency, attempt persistence, and dismissal.
  - `test_egypt_timezone_boundaries.py`: Local Cairo timezone and midnight handling.
  - `test_booking_player_identity.py`: exact-name identity, required `club_player`, historical `club_player_id`, recurrence copy, walk-in create, PATCH does not mutate ClubPlayer, idempotency.
