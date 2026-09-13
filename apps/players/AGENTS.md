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
5. **Unique current version per club+profile**: `UNIQUE(club, player_profile) WHERE is_current_version=True` — at most one **current** `ClubPlayer` row per pair. Historical versions (`is_current_version=False`) may coexist. There is no `deleted_at` / soft delete.
6. **Phone is the only merge key**: same phone = same `PlayerProfile`. Different phone = different `PlayerProfile`. No name-based matching, no automatic merging, no identity transfer.
7. **`PlayerProfile.full_name` is the player's preferred personal name.** Clubs do not control it. It is never auto-synchronized with `ClubPlayer.display_name`.
8. **Identity fields are immutable after create**: `club`, `player_profile`, `display_name`, `player_number`, `previous_version`. The only allowed post-create update is `is_current_version` (current → historical pointer). No `last_used_at`. No `updated_at`. No PATCH endpoint.
9. **No `StaffProfile` / `OwnerProfile`**: Do not create staff or owner profile tables here. `ClubMembership` is the operational actor; this app is purely for customer identity.

---

## ClubPlayer Lifecycle (Versioned, Append-Only) — Sprint 3 (Locked)

**`ClubPlayer` is an immutable historical version, not a normal editable entity and not a soft-deleted row.** A row is one version of "how this club knew this player." When club-local `display_name`/`player_number` changes, the existing row is never updated and never deleted.

```text
Before:
  ClubPlayer #10  is_current_version=True   display_name="Ahmed Ali"   player_number=7   previous_version=NULL

create_club_player_version(#10, display_name="Ahmed Salah", player_number=10)

After:
  ClubPlayer #10  is_current_version=False  display_name="Ahmed Ali"   player_number=7    ← historical, unchanged
  ClubPlayer #20  is_current_version=True   display_name="Ahmed Salah" player_number=10  previous_version=#10
```

Any `Booking.club_player_id` that already pointed at `#10` **keeps pointing at `#10`**. Bookings are historical events. They do **not** decide which version is current (`is_current_version`). They **do** decide which version was last used.

### Version fields

- `previous_version` — self-FK, `PROTECT`, nullable. NULL on the first version.
- `is_current_version` — boolean. Exactly one True per `(club, player_profile)`. Roster current identity.
- `created_at` — insert timestamp. There is **no** `updated_at` and **no** `last_used_at`. This row is not an editable entity; recency is derived from bookings.

### Last-used version (derived, not stored)

```text
latest Booking at this club
  where club_player.player_profile_id = this PlayerProfile.id
  ordered by Booking.created DESC, id DESC
→ that Booking.club_player  = last-used version
```

If this person has no booking at this club, last-used is unknown and recommendation falls back to the current roster version.

### Current uniqueness

```python
models.UniqueConstraint(
    fields=["club", "player_profile"],
    condition=models.Q(is_current_version=True),
    name="unique_current_club_player_profile",
)
```

### `create_club_player_version()` — identity-change transition

Locks the current row, sets `is_current_version=False`, creates a new current row with `previous_version` pointing at the old one. Same content is idempotent (returns the current row). Raises `ValueError` if the input row is not current. Wired from `POST /players/` when a current version already exists with different `display_name`/`player_number`.

### `get_or_create_club_player()` — current-version resolution

Returns the **current** version for `(club, player_profile)` unchanged. Does **not** create a new version because the caller passed a different name — that is `create_club_player_version()`'s job. Creates the first version only when none exists. Used by booking default resolution. Does not write recency onto ClubPlayer.

### `get_preferred_club_player()` / recommendation

Returns last-used version (`get_last_used_club_player()` — latest booking's `club_player` for this `player_profile_id` at this club). Falls back to the current roster version when there is no booking. Search responses expose that id as `recommended_club_player_id`.

### Immutability enforcement (defense in depth)

| Layer | Mechanism |
|---|---|
| **API** | `ClubPlayerViewSet` exposes only `list`, `create`, `retrieve`. PATCH is 403. |
| **Model (`save()`)** | Existing rows may only `save(update_fields⊆{is_current_version})`. |
| **QuerySet (`update()`)** | Same allowlist on bulk `.update()`. |

**Booking default:** `resolve_booking_club_player()` uses the current version. Staff may optionally send `club_player_id` to attach a historical version; that id must belong to this club and to the `PlayerProfile` identified by `customer_phone`. Recurring continuation still copies the anchor's exact version.

> [!NOTE]
> Recurring-booking continuation (`complete_booking()`) copies `club_player` from the anchor (Sprint 2). Soft delete was removed in Sprint 3.

---

## Service Layer

[`apps/players/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/services.py)

All service functions are **pure** — no role checks. Role enforcement lives in ViewSets and the permission matrix only.

| Function | Behavior |
|---|---|
| `find_or_create_player_profile(phone_number, full_name="")` | `get_or_create` by phone. Does **not** overwrite `full_name` on existing profiles. No merging. |
| `get_current_club_player(club, player_profile)` | Current version or `None`. |
| `get_or_create_club_player(club, player_profile, display_name="", player_number=None)` | Current version, or first version if none exists. Does not version on name change. |
| `create_club_player_version(club_player, display_name="", player_number=None)` | Marks current historical, creates new current with `previous_version`. |
| `get_last_used_club_player(club, player_profile)` | ClubPlayer on the latest Booking for this profile at this club, or `None`. |
| `get_preferred_club_player(club, player_profile)` | Last-used version, else current roster version. |
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
| MANAGER | list, retrieve, create | list, retrieve, create |
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

There is no PATCH payload. POSTing a new `display_name`/`player_number` for an existing current version records a new current version via `create_club_player_version()`. Same content is idempotent.

List/retrieve include `is_current_version`, `previous_version`, and `created_at`. They do **not** include `last_used_at` or `updated_at`. Filter `is_current_version=true` to see only current roster rows.

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
    {"id": 10, "display_name": "Ahmed Ali", "player_number": 7, "is_current_version": false, "previous_version": null, "created_at": "..."},
    {"id": 20, "display_name": "Ahmed Salah", "player_number": 10, "is_current_version": true, "previous_version": 10, "created_at": "..."}
  ],
  "recommended_club_player_id": 10
}
```

> [!NOTE]
> `has_account` is a computed bool. The raw `user_id` is **never** exposed (privacy boundary between authentication identity and customer identity).
> `recommended_club_player_id` is last-used (latest booking's `club_player` for this `player_profile_id` at this club). The example above is `10` because that version was on the latest booking even though `#20` is current. With no bookings it would be `20`.

---

## Booking Migration Path

> [!IMPORTANT]
> `Booking.customer_name` / `Booking.customer_phone` were **not** removed. They remain snapshot write fields. Operational consumers now read through `Booking.club_player` (see `apps/bookings/identity.py`).

- **Identity Foundation (Completed):** `PlayerProfile` and `ClubPlayer` exist.
- **Booking Phase A (Completed):** nullable `club_player` FK; `create_booking()` auto-resolves identity; no direct `player_profile` FK.
- **Sprint 1 / 2 (Completed):** append-only identity + recurrence `club_player` propagation. Soft delete from Sprint 1 is **superseded**.
- **Sprint 3 — Player Identity & Booking Integration (Completed):**
  - `ClubPlayer` is versioned: `previous_version`, `is_current_version`, `created_at`. No `deleted_at`. No `last_used_at`. No `updated_at`.
  - Default booking resolution uses the **current** version. Optional write-only `club_player_id` selects a historical version.
  - Last-used version is derived from the latest `Booking` for this `player_profile_id` at this club.
  - Player-profile list/retrieve returns `club_player_versions` + `recommended_club_player_id` (last-used, else current).
  - `customer_name` / `customer_phone` still present. `Booking.club_player` still nullable.
- **Sprint 4 — Consumer Migration To ClubPlayer Identity (Completed):** operational booking/dashboard/transaction/settlement **reads** use ClubPlayer (exact booking-time version) with snapshot fallback. API keys unchanged. Audit event JSON, BookingAttempt payloads, and idempotency still use snapshots. Reports do not display customer identity.
- **Booking Phase B (Future):** Authorization Spine v2 for bookings. Then snapshot column removal once remaining A/C write contracts are retired.

---

## Filters

[`apps/players/filters.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/filters.py)

`ClubPlayerFilterSet`: `search`, `player_number`, `is_current_version`.

`PlayerProfileFilterSet`: `search` across preferred name, phone, and this club's `ClubPlayer.display_name`.

Filters must never perform permission checks or membership lookups.

---

## Testing

Test suite: [`tests/players/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/players/)

| File | Coverage |
|---|---|
| `test_player_model.py` | Phone uniqueness/no merging, current-version uniqueness, `create_club_player_version()`, last-used from latest booking, immutability |
| `test_player_api.py` | list/create/retrieve, versioning POST, PATCH 403, profile versions + recommended id |
| `test_player_authorization.py` | Spine v2 club isolation |
| `tests/bookings/test_booking_player_identity.py` | Current-version booking default, historical `club_player_id` selection, history preservation, recurrence copy |

---

## Cross-App Dependencies & References

- `apps/accounts` — `User` model (nullable FK on `PlayerProfile`)
- `apps/clubs` — `Club` model (FK on `ClubPlayer`), `ClubMembership`
- `apps/common/authorization/` — Spine v2
- `apps/bookings` — `Booking.club_player` FK; default current version; optional historical id; recurrence copies the anchor version
- Architecture: [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
- [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md), [`ADR-002`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-002-booking-identity-and-authorization-migration.md)
