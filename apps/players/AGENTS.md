# Players — Agent Guide

## Responsibility

`apps/players/` owns the **customer identity layer**:

- [`PlayerProfile`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/models.py) — global customer identity, keyed by phone number. A player does not need a user account.
- [`ClubPlayer`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/models.py) — club-local representation of a player (display name, jersey number). Each club labels and numbers players independently.

**Read the security architecture and architectural decisions for the full identity boundary:**
→ [`docs/architecture/security-architecture-v1.md §1`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
→ [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
→ [`docs/architecture/adr/ADR-001-player-identity-and-booking-link.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md)
→ [`docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)

---

## Identity Boundary

```text
Authentication Identity
        │
       User (apps/accounts)
        │
        ├── Club Operational Actor ──► ClubMembership (apps/clubs)
        │
        └── Customer Identity ──► PlayerProfile ──► ClubPlayer  ← THIS APP
```

| Model | Boundary | Authorization |
|---|---|---|
| `PlayerProfile` | **Global** — no club boundary | No `authorization_config`. Club membership gates API access but the row itself is shared. |
| `ClubPlayer` | **Club-scoped** | `authorization_config` with `default_scope="club"` (Spine v2). |

---

## Domain Invariants

1. **Phone uniqueness**: `PlayerProfile.phone_number` is globally unique. It is the identity key.
2. **Player ≠ account**: `PlayerProfile.user` is nullable. A player can exist with `user=NULL` and `verified=False`.
3. **Club controls its own naming**: Two clubs may have different `display_name` and `player_number` for the same `PlayerProfile`. Both are correct.
4. **Cross-club isolation**: `ClubPlayer` rows are invisible across clubs. The Spine v2 `scoped_queryset()` enforces this structurally — clubs cannot read or write each other's `ClubPlayer` rows.
5. **Unique per club, per active identity**: `UNIQUE(club, player_profile) WHERE deleted_at IS NULL` — at most one **active** `ClubPlayer` row per (club, profile) pair. Historical (soft-deleted) rows for the same pair may coexist alongside the active one — see "ClubPlayer Lifecycle (Append-Only)" below.
6. **`player_profile` FK is immutable, and so is everything else**: After a `ClubPlayer` is created, **none** of its identity fields (`club`, `player_profile`, `display_name`, `player_number`) can change — not just `player_profile`. There is no PATCH/update endpoint. See "ClubPlayer Lifecycle (Append-Only)" below.
7. **No `StaffProfile` / `OwnerProfile`**: Do not create staff or owner profile tables here. `ClubMembership` is the operational actor; this app is purely for customer identity.

---

## ClubPlayer Lifecycle (Append-Only) — Sprint 1 (Locked)

**`ClubPlayer` is a historical identity record, not a normal editable entity.** A `ClubPlayer` row represents the identity state that was in effect for bookings made while it was active. When a player's club-local `display_name`/`player_number` needs to change, the existing row is **never** updated in place.

```text
Before:
  ClubPlayer #10  (active)   display_name="Ahmed Ali"   player_number=7

supersede_club_player(club_player=#10, display_name="Ahmed Salah", player_number=11)

After:
  ClubPlayer #10  (deleted_at=<timestamp>)   display_name="Ahmed Ali"   player_number=7   ← unchanged, historical
  ClubPlayer #20  (active)                   display_name="Ahmed Salah" player_number=11  ← new current identity
```

Any `Booking.club_player_id` that already pointed at `#10` **keeps pointing at `#10`** — that FK *is* the historical snapshot as of when the booking was made. `Booking` never needs to be touched when a `ClubPlayer` is superseded.

### Soft delete field

- `ClubPlayer.deleted_at` (nullable `DateTimeField`). `NULL` = active/current identity. Non-`NULL` = historical/superseded — permanently retained, never physically deleted, never resurrected.
- `ClubPlayer.is_active` — convenience property, `True` iff `deleted_at is None`.

### Active uniqueness (not global uniqueness)

`Meta.constraints` declares a **conditional** unique constraint:

```python
models.UniqueConstraint(
    fields=["club", "player_profile"],
    condition=models.Q(deleted_at__isnull=True),
    name="unique_active_club_player_profile",
)
```

This replaces the original unconditional `UNIQUE(club, player_profile)` from the identity-foundation phase. Multiple **deleted** rows may coexist for the same (club, profile) pair (one per historical version); only **one active row** is ever allowed at a time. Supported on both PostgreSQL and SQLite (Django emits it as a partial/conditional index on both backends) — no backend-specific migration branching was needed here, unlike the Booking↔ClubPlayer composite FK (see `apps/bookings/AGENTS.md`).

### `supersede_club_player()` — the only supported identity-change transition

```python
def supersede_club_player(club_player: ClubPlayer, display_name="", player_number=None) -> ClubPlayer:
    ...
```

- Locks the existing row (`select_for_update()`), verifies it is currently active (raises `ValueError` if it's already deleted — you cannot supersede a row that isn't the active identity), soft-deletes it (`deleted_at = now()`), then creates a brand-new active `ClubPlayer` for the same `(club, player_profile)` pair with the new `display_name`/`player_number`. Runs inside `transaction.atomic()`.
- **Not yet wired to an HTTP endpoint** in this sprint — it exists as a service function only, callable from Django shell/admin scripting or a future ViewSet action. See "Remaining Work" below.

### `get_or_create_club_player()` — updated to respect the active/historical split

- Only ever returns/matches the **active** row (`deleted_at__isnull=True`) for a `(club, player_profile)` pair.
- If no active row exists — either because none was ever created, or because the only prior row was superseded — a **brand-new** active row is created. A deleted row is never returned and never resurrected.
- Race-safety: mirrors Django's own `get_or_create()` pattern (`try create() / except IntegrityError: re-fetch`), since the conditional unique constraint can still legitimately raise under concurrent creation for the same pair.

### Immutability enforcement (defense in depth)

Three independent layers, from outermost to innermost — no single layer is trusted alone:

| Layer | Mechanism |
|---|---|
| **API** | `ClubPlayerViewSet` exposes only `list`, `create`, `retrieve`. No `update`/`partial_update` action, no `ClubPlayerUpdateSerializer`, no `PATCH` route in `urls.py`, no `update`/`partial_update` grant in `ROLE_PERMISSIONS["ClubPlayerViewSet"]` for any role. A `PATCH` request to `/players/{pk}/` is rejected with `403` (the permission check runs before Django's method-not-allowed dispatch, since the action isn't bound to any HTTP verb at all). |
| **Model (`save()`)** | `ClubPlayer.save()` raises `ValueError` on any save of an existing row (`self.pk is not None`) unless `update_fields` is explicitly passed and restricted to `{"deleted_at", "updated_at"}`. This blocks the common accidental-mutation pattern (`instance.display_name = "x"; instance.save()`) at the model layer, independent of whatever calls it (service, shell, future code). |
| **QuerySet (`update()`)** | `ClubPlayerQuerySet.update()` (via `objects = ClubPlayerQuerySet.as_manager()`) applies the same field restriction to bulk `.filter(...).update(...)` calls, which bypass `save()` entirely in Django. Without this, `ClubPlayer.objects.filter(pk=x).update(display_name="y")` would silently succeed despite the `save()` guard. |

**Design decision or the "cleanest approach" (as required by the sprint spec):** immutability is enforced by restricting `update_fields`/`update()` kwargs to an explicit allowlist, rather than (a) a DB trigger, (b) diffing against a re-fetched DB copy on every `save()` (extra query per save), or (c) a signal-based guard (signals don't fire for `.update()` either, and the root `AGENTS.md` prohibits relying on signals for domain logic). The allowlist approach is a pure Python-level check, requires no extra queries, and is enforced identically at both the single-instance and bulk-update code paths.

**Booking consumes only active `ClubPlayer` identities:** every `Booking.club_player` reference resolved by `apps.bookings.services` (`create_booking()` via `resolve_booking_club_player()`, and the demo/dev fixture seeder) goes through `get_or_create_club_player()` above — it can therefore only ever be linked to an **active** row at resolution time. A soft-deleted `ClubPlayer` is never assigned to a *new* booking. This does not retroactively change any *existing* `Booking.club_player_id` — that FK is a historical snapshot and is untouched by a later supersede (see `apps/bookings/AGENTS.md` "Customer Identity Architecture").

### Remaining work (explicitly deferred — not part of this sprint)

- No API endpoint calls `supersede_club_player()` yet. A future sprint must decide the shape (e.g. `POST /players/{pk}/supersede/`) and permissions.
- `ClubPlayerViewSet.list()`/`.retrieve()` do **not** filter out soft-deleted rows — a club's roster currently includes historical/deleted `ClubPlayer` rows alongside active ones. Whether to default-filter to active-only, or expose `deleted_at`/an `include_deleted` query param, is an open API design question for the next sprint.

> [!NOTE]
> The recurring-booking continuation gap noted here previously (`complete_booking()` next-occurrence creation not carrying `club_player` forward) was **fixed in Booking Identity Enforcement — Sprint 2**. See `apps/bookings/AGENTS.md` "Booking-Native Recurrence".

---

## Service Layer

[`apps/players/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/services.py)

All service functions are **pure** — no role checks. Role enforcement lives in ViewSets and the permission matrix only.

| Function | Behavior |
|---|---|
| `find_or_create_player_profile(phone_number, full_name="")` | `get_or_create` by phone. Returns `(PlayerProfile, created)`. Does **not** overwrite `full_name` on existing profiles. |
| `get_or_create_club_player(club, player_profile, display_name="", player_number=None)` | Finds the **active** (`deleted_at IS NULL`) `ClubPlayer` for `(club, player_profile)`, or creates one. Never returns/resurrects a soft-deleted row. Returns `(ClubPlayer, created)`. |
| `supersede_club_player(club_player, display_name="", player_number=None)` | Soft-deletes `club_player` and creates a new active `ClubPlayer` for the same `(club, player_profile)` pair. The only supported way to change identity information. Raises `ValueError` if `club_player` is already deleted. Returns the new `ClubPlayer`. |
| `link_player_to_user(player_profile, user)` | Sets `player_profile.user = user`. Idempotent if same user. Raises `SlotyAPIException(PLAYER_ALREADY_LINKED)` if already linked to a different user. |

All write functions wrap their DB writes in `transaction.atomic()`.

---

## Authorization

### `ClubPlayer`

Wired to the **Authorization Spine v2** via `SlotyScopedResourceMixin`:

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
    },
    "default_scope": "club",
    "select_related": ("club", "player_profile"),
    "prefetch_related": (),
}
```

- `default_scope = "club"` — no court scope, never court-scoped.
- Club boundary is enforced before any filter or pagination runs.

### `PlayerProfile`

**No `authorization_config`**. `PlayerProfile` is a global model — its rows are not club-scoped at the DB level. The API layer enforces club membership as the gate condition (requires authenticated club member). The list endpoint is narrowed to profiles with at least one `ClubPlayer` in the current club.

### Permission Matrix

Entries in [`apps/common/authorization/matrix.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/matrix.py):

| Role | PlayerProfileViewSet | ClubPlayerViewSet |
|---|---|---|
| ADMIN | list, retrieve, create | list, retrieve, create |
| OWNER | list, retrieve, create | list, retrieve, create |
| MANAGER | list, retrieve, create | list, retrieve, create |
| STAFF | list, retrieve, create | list, retrieve, create |

No `update`/`partial_update` for any role — removed in Sprint 1 (ClubPlayer Historical Identity Foundation). See "ClubPlayer Lifecycle (Append-Only)" above.

No `destroy` action either. Player deletion has FK cascade implications (future bookings) and requires a deliberate separate decision.

---

## API Endpoints

All endpoints are club-scoped under `/api/v1/clubs/{club_slug}/`.

| Method | URL | ViewSet | Action |
|---|---|---|---|
| GET | `clubs/{club_slug}/players/` | `ClubPlayerViewSet` | list |
| POST | `clubs/{club_slug}/players/` | `ClubPlayerViewSet` | create |
| GET | `clubs/{club_slug}/players/{pk}/` | `ClubPlayerViewSet` | retrieve |
| GET | `clubs/{club_slug}/player-profiles/` | `PlayerProfileViewSet` | list (club-linked only) |
| POST | `clubs/{club_slug}/player-profiles/` | `PlayerProfileViewSet` | create (find-or-create; 201 new / 200 existing) |
| GET | `clubs/{club_slug}/player-profiles/{pk}/` | `PlayerProfileViewSet` | retrieve |

### ClubPlayer create payload

```json
{
  "phone_number": "+201012345678",
  "full_name": "Ahmed Hassan",       // optional — only used if new PlayerProfile created
  "display_name": "Ahmed H",          // optional — club-specific name
  "player_number": 10                 // optional — club-internal number
}
```

There is no update/partial_update payload — `ClubPlayer` has no PATCH endpoint. See "ClubPlayer Lifecycle (Append-Only)" above for how identity changes are handled instead (`supersede_club_player()`, not yet wired to an HTTP endpoint).

### PlayerProfile response shape

```json
{
  "id": 1,
  "phone_number": "+201012345678",
  "full_name": "Ahmed Hassan",
  "verified": false,
  "has_account": false,    // true if user FK is populated
  "created_at": "...",
  "updated_at": "..."
}
```

> [!NOTE]
> `has_account` is a computed bool. The raw `user_id` is **never** exposed (privacy boundary between authentication identity and customer identity).

---

## Booking Migration Path

> [!IMPORTANT]
> `Booking.customer_name` / `Booking.customer_phone` were **not** removed. They are locked-in permanent historical snapshot fields (see `apps/bookings/AGENTS.md` "Customer Identity Architecture").

- **Identity Foundation (Completed):** `PlayerProfile` and `ClubPlayer` exist.
- **Booking Phase A (Completed):**
  - Added a single nullable `club_player` FK to `Booking` — **not** a direct `player_profile` FK. `Booking` belongs to a `ClubPlayer`; `PlayerProfile` is reachable only transitively via `club_player.player_profile`. This refines the original [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md) Decision 1 (two FKs) — see the ADR-002 addendum.
  - `create_booking()` auto-resolves identity via `find_or_create_player_profile()` then `get_or_create_club_player()`; the frontend never sends `player_profile_id` / `club_player_id`.
  - `customer_name` / `customer_phone` remain forever as historical snapshot fields — reviewed and kept intentionally (audit, dashboard calendar, transaction receipts, search), not merely preserved out of migration caution.
  - No booking authorization/ViewSet/permission changes — that remains Phase B.
- **ClubPlayer Historical Identity Foundation — Sprint 1 (Completed):**
  - `ClubPlayer` became an append-oriented historical identity record: soft delete (`deleted_at`), conditional active-uniqueness, `supersede_club_player()` service, and model/queryset-level immutability enforcement. See "ClubPlayer Lifecycle (Append-Only)" above.
  - `Booking.customer_name` / `Booking.customer_phone` were **not** touched — still present, still permanent snapshots (see `apps/bookings/AGENTS.md` "Customer Identity Architecture"). This sprint changed only how `ClubPlayer` itself behaves, not `Booking`.
  - No new API endpoint calls `supersede_club_player()` yet — deferred (see "Remaining work" above).
- **Booking Identity Enforcement — Sprint 2 (Completed):**
  - Fixed the recurrence-continuation gap: `complete_booking()`'s next-occurrence creation now copies `club_player` from the anchor booking instead of leaving it `NULL`.
  - Audited every `Booking.objects.create()` / `.update_or_create()` / `.bulk_create()` call site in the codebase. The dev/demo fixture seeder (`apps.accounts.management.commands.seed_demo_data`) was updated to also call `resolve_booking_club_player()` so seeded bookings carry a real identity link; all other direct-creation call sites are test fixtures (out of scope).
  - Confirmed (with new regression tests) that `create_booking()` never resolves a soft-deleted `ClubPlayer` end-to-end through the booking service, not just at the `apps/players` service level (Sprint 1 already covered the latter).
  - Confirmed the `Booking.club_id == Booking.club_player.club_id` invariant and the "frontend never sends `club_player_id`/`player_profile_id`" API contract were already correctly enforced from Phase A — no changes needed there.
  - `Booking.customer_name` / `Booking.customer_phone` were **not** touched — still permanent snapshots.
- **Booking Phase B (Future sprint):**
  - Migrate `BookingViewSet` / `BookingAttemptViewSet` to Authorization Spine v2.
  - See [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md) and [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md).

---

## Filters

[`apps/players/filters.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/filters.py)

`ClubPlayerFilterSet`:
- `search` — icontains on `display_name`, `player_profile__phone_number`, `player_profile__full_name`
- `player_number` — exact match

Filters must never perform permission checks or membership lookups.

---

## Testing

Test suite: [`tests/players/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/players/)

| File | Coverage |
|---|---|
| `test_player_model.py` | Phone uniqueness, nullable user FK, user linking, cross-club independence, service functions, ClubPlayer active-uniqueness, soft delete, `save()`/`update()` immutability enforcement, `supersede_club_player()` |
| `test_player_api.py` | list/create/retrieve, cross-club isolation, role matrix, same-phone-two-clubs, idempotent create, filters, 200/201 semantics, PATCH rejection (403, no update action bound) |
| `test_player_authorization.py` | `authorization_config` contract, `scoped_queryset` club isolation without HTTP layer |
| `tests/bookings/test_booking_player_identity.py` (`apps/bookings`) | Cross-domain: a `Booking.club_player_id` survives `supersede_club_player()` unchanged; a booking created *after* a supersede resolves the new active `ClubPlayer`; (Sprint 2) `create_booking()` never reuses a soft-deleted `ClubPlayer`; `complete_booking(continue_recurring=True)` propagates `club_player` to the next occurrence |

---

## Cross-App Dependencies & References

- `apps/accounts` — `User` model (nullable FK on `PlayerProfile`)
- `apps/clubs` — `Club` model (FK on `ClubPlayer`), `ClubMembership` (resolved by spine for access context)
- `apps/common/authorization/` — Spine v2 (`SlotyScopedResourceMixin`, `scoped_queryset`, `load_authorization_config`)
- `apps/bookings` — `Booking.club_player` FK consumer; calls `find_or_create_player_profile()` / `get_or_create_club_player()` from `create_booking()` (Booking Phase A, complete). A `Booking.club_player_id` is unaffected by a later `supersede_club_player()` call on that row — the FK is the historical snapshot (Sprint 1). Recurring-booking continuations copy `club_player` from their anchor rather than re-resolving it (Sprint 2).
- Architecture reference: [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
- Architectural Decision Records:
  - [`ADR-001 (Player Identity & Booking Link)`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md)
  - [`ADR-002 (Booking Identity & Authorization Migration)`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)
