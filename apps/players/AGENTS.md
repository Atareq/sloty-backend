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
        ├── Operational Profile ──► Profile (apps/profiles)
        │
        └── Customer Identity ──► PlayerProfile ──► ClubPlayer  ← THIS APP
```

| Model | Boundary | Authorization |
|---|---|---|
| `PlayerProfile` | **Global** — no club boundary | No `authorization_config`. The enabled operational Profile's club scope gates API access but the row itself is shared. |
| `ClubPlayer` | **Club-scoped** | `authorization_config` with `default_scope="club"` (Spine v2). |

---

### Core Principle: SIMPLICITY WINS

Keep the implementation direct, minimal, and clean.
- The latest `Booking.club_player` relationship is the single source of truth for the current `ClubPlayer` identity.
- No version chains, no stored version pointers (`previous_version`), and no current version flags (`is_current_version`).
- Direct customer record model (`display_name`, `player_number`).

---

## Domain Invariants

1. **Phone uniqueness**: `PlayerProfile.phone_number` is globally unique. It is the identity key.
2. **Player ≠ account**: `PlayerProfile.user` is nullable. A player can exist with `user=NULL` and `verified=False`.
3. **Club controls its own naming**: Two clubs may have different `display_name` and `player_number` for the same `PlayerProfile`. Both are correct.
4. **Cross-club isolation**: `ClubPlayer` rows are invisible across clubs. The Spine v2 `scoped_queryset()` enforces this structurally — clubs cannot read or write each other's `ClubPlayer` rows.
5. **Identity Source of Truth**: The latest `Booking.club_player` relationship is the source of truth for current `ClubPlayer` identity. If no booking exists for that player in that club, fallback to the latest `ClubPlayer` record for that player/club (`order_by("-id").first()`).
6. **Phone is the only merge key**: same phone = same `PlayerProfile`. Different phone = different `PlayerProfile`. No name-based matching, no automatic merging, no identity transfer.
7. **`PlayerProfile.full_name` is the player's preferred personal name.** Clubs do not control it. It is never auto-synchronized with `ClubPlayer.display_name`.
8. **Historical booking integrity**: Historical `Booking.club_player` relationships remain intact. No soft delete. No `previous_version`, `is_current_version`, or `created_at` metadata fields on `ClubPlayer`.
9. **Separate identity boundaries**: Do not create or modify operational Profile tables here. `apps/profiles` owns owner/staff/admin authority; this app is purely for customer identity.

---

## ClubPlayer & Customer Identity Architecture

**`ClubPlayer` is a direct club-local customer identity record (`display_name`, `player_number`).**

```text
Phone Number
      │
      ▼
PlayerProfile (global)
      │
      ▼
ClubPlayer (club, player_profile, display_name, player_number)
      │
      ▼
Booking.club_player  ← historical booking anchor & source of truth for current identity
```

- **No stored versioning metadata**: `ClubPlayer` does NOT have `previous_version`, `is_current_version`, or `created_at`.
- **Current identity resolution**:
  1. Primary: Latest `Booking.club_player` for `(club, player_profile)` ordered by `-created`, `-id`.
  2. Fallback: Latest `ClubPlayer` record for `(club, player_profile)` ordered by `-id`.
- **Historical bookings**: Bookings preserve the exact `ClubPlayer` row they were created with.
- **Roster creation & updates**: POSTing to `/players/` checks for an existing identical `(club, player_profile, display_name, player_number)` row to reuse; if any field differs, a new row is inserted.

---

## Service Layer

[`apps/players/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/services.py)

All service functions are **pure** — no role checks. Role enforcement lives in ViewSets and the permission matrix only.

| Function | Behavior |
|---|---|
| `find_or_create_player_profile(phone_number, full_name="")` | `get_or_create` by phone. Does **not** overwrite `full_name` on existing profiles. No merging. |
| `get_current_club_player(club, player_profile)` | Primary lookup from latest `Booking.club_player`, fallback to latest `ClubPlayer` row (`order_by("-id").first()`). |
| `get_or_create_club_player(club, player_profile, display_name="", player_number=None)` | Returns current `ClubPlayer` if one exists, otherwise creates a new `ClubPlayer`. |
| `get_last_used_club_player(club, player_profile)` | `ClubPlayer` on the latest Booking for this profile at this club, or `None`. |
| `get_preferred_club_player(club, player_profile)` | Last-used `ClubPlayer` from bookings, else latest `ClubPlayer` row. |
| `link_player_to_user(player_profile, user)` | Sets `player_profile.user`. Raises `PLAYER_ALREADY_LINKED` on conflict. |

All write functions wrap their DB writes in `transaction.atomic()`.


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
| STAFF | list, retrieve, create | list, retrieve, create |

No `update`/`partial_update` for any role. See "ClubPlayer Lifecycle (Versioned, Append-Only)" above.

No `destroy` action either. ClubPlayer versions are never deleted.

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

There is no PATCH payload. POSTing a new `display_name`/`player_number` for a player reuses an existing identical record or creates a new one.

List/retrieve include `id`, `club`, `player_profile`, `display_name`, and `player_number`.

### PlayerProfile search/list response shape

```json
{
  "id": 1,
  "phone_number": "+201012345678",
  "full_name": "Ahmed Hassan",
  "verified": false,
  "has_account": false,
  "created_at": "...",
  "updated_at": "...",
  "club_player_versions": [
    {"id": 10, "display_name": "Ahmed Ali", "player_number": 7},
    {"id": 20, "display_name": "Ahmed Salah", "player_number": 10}
  ],
  "recommended_club_player_id": 10
}
```

> [!NOTE]
> `has_account` is a computed bool. The raw `user_id` is **never** exposed (privacy boundary between authentication identity and customer identity).
> `recommended_club_player_id` is last-used (latest booking's `club_player` for this `player_profile_id` at this club). The example above is `10` because that record was on the latest booking. With no bookings it falls back to the latest `ClubPlayer` record (`20`).

---

## Booking Identity

`Booking.club_player` is required. There is no `Booking.customer_name` / `Booking.customer_phone` column and no `Booking → PlayerProfile` FK.

Walk-in create still sends `customer_name` / `customer_phone`; the backend resolves `PlayerProfile` (by phone) and the current `ClubPlayer` record unless `club_player_id` selects a specific record.

- **Identity Foundation:** `PlayerProfile` and `ClubPlayer` exist.
- **ClubPlayer simplification:** direct customer model; latest `Booking.club_player` is the source of truth for current identity; no version chain or stored version metadata.
- **Booking identity finalization:** `club_player` NOT NULL, `on_delete=PROTECT`; snapshot columns removed; operational reads and new audit JSON use ClubPlayer; BookingAttempt payload fields remain; PATCH is notes-only.

---

## Filters

[`apps/players/filters.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/filters.py)

`ClubPlayerFilterSet`: `search`, `player_number`.

`PlayerProfileFilterSet`: `search` across preferred name, phone, and this club's `ClubPlayer.display_name`.

Filters must never perform permission checks or membership lookups.

---

## Testing

Test suite: [`tests/players/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/players/)

| File | Coverage |
|---|---|
| `test_player_model.py` | Phone uniqueness/no merging, current identity from latest booking, latest record fallback |
| `test_player_api.py` | list/create/retrieve, POST reuse/create, PATCH 403, profile club_player_versions + recommended id |
| `test_player_authorization.py` | Spine v2 club isolation |
| `tests/bookings/test_booking_player_identity.py` | Current booking identity source of truth, historical booking preservation, recurrence copy |

---

## Cross-App Dependencies & References

- `apps/accounts` — `User` model (nullable FK on `PlayerProfile`)
- `apps/clubs` — `Club` model (FK on `ClubPlayer`)
- `apps/profiles` — operational role authority; it is separate from customer identity
- `apps/common/authorization/` — Spine v2
- `apps/bookings` — `Booking.club_player` FK; default current version; optional historical id; recurrence copies the anchor version
- Architecture: [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
- [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md), [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)
