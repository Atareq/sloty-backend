# Players — Agent Guide

## Responsibility

`apps/players/` owns the **customer identity layer**:

- [`PlayerProfile`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/models.py) — global customer identity and real `Profile` child, keyed by phone number. A player does not need a user account.
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
       User (apps/accounts, optional for players)
        │
        ▼
Profile (apps/profiles)
        ├── OwnerProfile / StaffProfile / AdminProfile  (management)
        └── PlayerProfile ──► ClubPlayer  ← THIS APP
```

| Model | Boundary | Authorization |
|---|---|---|
| `PlayerProfile` | **Global** — no club boundary | No `authorization_config`. It has `Profile.role == PLAYER`, but `PLAYER` is not granted management permissions. Owner/Staff/Admin club scope gates the management APIs that create or view rows. |
| `ClubPlayer` | **Club-scoped** | `authorization_config` with `default_scope="club"` (Spine v2). |

---

### Core Principle: SIMPLICITY WINS

Keep the implementation direct, minimal, and clean.
- ClubPlayer identity is `club + player_profile + exact display_name`. A different exact name is a new row.
- No version chains, no stored version pointers (`previous_version`), and no current version flags (`is_current_version`).
- Last-used is derived from the latest `Booking.club_player`; it is recency, not identity equality.

---

## Domain Invariants

1. **Phone uniqueness**: `PlayerProfile.phone_number` is globally unique. It is the identity key.
2. **Player ≠ account**: `PlayerProfile` inherits nullable `Profile.user`. A player can exist with `user=NULL`, `role=PLAYER`, and `verified=False`.
3. **Club controls its own naming**: Two clubs may have different `display_name` and `player_number` for the same `PlayerProfile`. Both are correct.
4. **Cross-club isolation**: `ClubPlayer` rows are invisible across clubs. The Spine v2 `scoped_queryset()` enforces this structurally — clubs cannot read or write each other's `ClubPlayer` rows.
5. **Exact ClubPlayer identity**: `UNIQUE(club, player_profile, display_name)`. Same club and PlayerProfile may have multiple ClubPlayer rows when the exact `display_name` differs. Identical triples are rejected. The latest `Booking.club_player` is the source of truth for last-used identity; if no booking exists, fall back to the latest ClubPlayer for that club/profile (`order_by("-id").first()`).
6. **Phone is the only merge key**: same phone = same `PlayerProfile`. Different phone = different `PlayerProfile`. No name-based matching, no automatic merging, no identity transfer.
7. **`PlayerProfile.full_name` is the player's preferred personal name.** Clubs do not control it. It is never auto-synchronized with `ClubPlayer.display_name`.
8. **Historical booking integrity**: Historical `Booking.club_player` relationships remain intact. ClubPlayer identity fields are not patched after create. There is no `previous_version`, `is_current_version`, `last_used_at`, or `deleted_at`.
9. **Separate identity boundaries**: Do not merge `User`, `Profile`, `PlayerProfile`, `ClubPlayer`, or `Booking`. `PlayerProfile` is the customer identity child of `Profile`; `ClubPlayer` is still the club-local customer record.

---

## ClubPlayer identity

`ClubPlayer` is a club-local exact-name identity record (`display_name`, `player_number`). It is not a version chain. A different exact `display_name` is a new row. Existing rows are not renamed.

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
Booking.club_player  ← historical booking preserves which ClubPlayer was used
```

- **Uniqueness**: `UNIQUE(club, player_profile, display_name)`.
- **Same exact name** → reuse that ClubPlayer.
- **Different exact name** (`"Ahmed"` vs `"Ahmed Ali"`) → new ClubPlayer.
- **Last-used (derived)**: latest `Booking.club_player` for `(club, player_profile)` ordered by `-created`, `-id`. If no booking exists, recommendation falls back to the latest ClubPlayer row (`order_by("-id").first()`). This is recency, not identity equality.
- **Roster create**: `POST /players/` reuses the exact triple or inserts a new row.
- There is no `previous_version`, `is_current_version`, `last_used_at`, or `deleted_at`.

`resolve_club_player()` / `resolve_booking_club_player()` resolve bookings by phone then exact `display_name`. Optional `club_player_id` must belong to the club and match supplied phone/name when those are sent. Recurring continuation copies the anchor's `club_player`.

| Layer | Mechanism |
|---|---|
| **API** | `ClubPlayerViewSet` exposes only `list`, `create`, `retrieve`. PATCH is 403. |
| **Identity create** | `get_or_create_club_player(club, player_profile, display_name)` uses the unique triple. |
---

## Service Layer

[`apps/players/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/services.py)

All service functions are **pure** — no role checks. Role enforcement lives in ViewSets and the permission matrix only.

| Function | Behavior |
|---|---|
| `find_or_create_player_profile(phone_number, full_name="")` | `get_or_create` by phone with `role=PLAYER` and `user=NULL` for new rows. Does **not** create a User and does **not** overwrite `full_name` on existing profiles. Concurrent unique-phone races reload the existing row. |
| `get_or_create_club_player(club, player_profile, display_name, player_number=None)` | Exact triple `(club, player_profile, display_name)`. Same name reuses; different name creates. Concurrent unique races reload the existing row. |
| `get_current_club_player(club, player_profile)` | Last-used from latest Booking, else latest ClubPlayer row. Recency helper, not identity equality. |
| `get_last_used_club_player(club, player_profile)` | ClubPlayer on the latest Booking for this profile at this club, or `None`. |
| `get_preferred_club_player(club, player_profile)` | Last-used from bookings, else latest ClubPlayer row. |
| `link_player_to_user(player_profile, user)` | Sets the inherited `Profile.user` for future account-linking flows. Raises on conflicting links. Not used during normal player creation. |

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
| STAFF | list, retrieve, create | list, retrieve, create |

No `update`/`partial_update` for any role.

No `destroy` action either. ClubPlayer rows are never deleted.

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

There is no PATCH payload. POSTing a new exact `display_name` for the same phone creates a new ClubPlayer; the same exact name reuses the existing row.

List/retrieve include `id`, `club`, `player_profile`, `display_name`, and `player_number`.

### PlayerProfile search/list response shape

```json
{
  "id": 1,
  "phone_number": "+201012345678",
  "full_name": "Ahmed Hassan",
  "verified": false,
  "has_account": false,
  "created": "...",
  "modified": "...",
  "club_player_versions": [
    {"id": 10, "display_name": "Ahmed Ali", "player_number": 7},
    {"id": 20, "display_name": "Ahmed Salah", "player_number": 10}
  ],
  "recommended_club_player_id": 10
}
```

> [!NOTE]
> `has_account` is a computed bool. The raw `user_id` is **never** exposed (privacy boundary between authentication identity and customer identity).
> `recommended_club_player_id` is last-used (latest booking's `club_player` for this `player_profile_id` at this club). With no bookings it falls back to the latest `ClubPlayer` row.

---

## Booking Identity

`Booking.club_player` is required. There is no `Booking.customer_name` / `Booking.customer_phone` column and no `Booking → PlayerProfile` FK.

Walk-in create still sends `customer_name` / `customer_phone`; the backend resolves `PlayerProfile` by phone and `ClubPlayer` by exact `display_name`. Optional `club_player_id` selects a specific existing row.

- **Identity Foundation:** `PlayerProfile` and `ClubPlayer` exist.
- **ClubPlayer identity:** exact `club + player_profile + display_name`; historical bookings keep their `club_player` FK.
- **Booking identity finalization:** `club_player` NOT NULL, `on_delete=PROTECT`; snapshot columns removed. Booking PATCH may resolve and assign `customer_name` / `customer_phone` through the canonical ClubPlayer resolver; it never mutates an existing ClubPlayer or PlayerProfile.

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
| `test_player_model.py` | Phone uniqueness/no merging, exact-name ClubPlayer uniqueness, last-used from latest booking |
| `test_player_api.py` | list/create/retrieve, POST reuse/create, PATCH 403, recommended id |
| `test_player_authorization.py` | Spine v2 club isolation |
| `tests/bookings/test_booking_player_identity.py` | Exact-name booking resolution, historical `club_player_id`, recurrence copy |

---

## Cross-App Dependencies & References

- `apps/accounts` — `User` model (nullable FK on `PlayerProfile`)
- `apps/clubs` — `Club` model (FK on `ClubPlayer`)
- `apps/profiles` — operational role authority; it is separate from customer identity
- `apps/common/authorization/` — Spine v2
- `apps/bookings` — `Booking.club_player` FK; exact-name resolution; optional historical id; recurrence copies the anchor row
- Architecture: [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
- [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md), [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)
