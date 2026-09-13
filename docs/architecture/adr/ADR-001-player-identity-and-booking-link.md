# ADR-001: Player Identity and Booking Link Architecture

**Status:** Accepted
**Date:** 2026-09-13
**Deciders:** Backend Engineering
**Consulted:** Security Architecture v1

---

## 1. Context

In Sloty, courts are booked on behalf of sports players/customers. Prior to this decision:
- `User` represented authenticated accounts (credentials, login tokens, platform admin authority).
- `ClubMembership` represented operational club actors (`OWNER`, `MANAGER`, `STAFF`).
- Bookings stored customers as denormalized, free-text strings: `customer_name` (`CharField`) and `customer_phone` (`PhoneNumberField`).

This led to several core architectural tensions:
1. **Walk-in customer reality**: Most court renters are walk-in players who do not have, and may never want, a Sloty login account.
2. **Account vs. Customer conflation**: Forcing every player to register a `User` would create friction and pollute authentication tables.
3. **Cross-club naming**: The same real-world player (identified by mobile phone number) plays at multiple independent clubs, but clubs frequently identify players with distinct local display names (e.g., nicknames, jersey numbers).
4. **Data silos vs. Duplication**: If each club creates its own independent customer record without a global identity anchor, global analytics and future self-service player portals become impossible. Conversely, if clubs share customer records directly, cross-club privacy and tenant isolation are breached.

---

## 2. Decision

We introduce a two-tier customer identity architecture separating global identity from club-local representation:

```text
Authentication Identity
        │
       User (apps/accounts)
        │
        ├── Operational Club Actor ──► ClubMembership (apps/clubs)
        │
        └── Customer Identity
                    │
              PlayerProfile (Global human identity)
                    │
                ClubPlayer (Club-specific representation)
                    │
                   Club (Tenant boundary)
```

### Models

1. **`PlayerProfile` (`apps/players/models.py`)**:
   - Represents the global real-world person.
   - Primary key anchor: `phone_number UNIQUE`.
   - `user` is an optional, nullable foreign key to `User`.
   - `verified` boolean tracks phone ownership independently from account authentication.
   - Declares **no** `authorization_config` (not scoped to any single club).

2. **`ClubPlayer` (`apps/players/models.py`)**:
   - Represents how an individual club names, numbers, and organizes that player.
   - Foreign keys: `club` (tenant boundary) and `player_profile` (global anchor).
   - Unique constraint: `UNIQUE(club, player_profile)`.
   - Club-local metadata: `display_name`, `player_number`.
   - Integrates with Authorization Spine v2 via `authorization_config` with `default_scope = "club"`.

---

## 3. Reasons

1. **Separate authentication from customer identity**:
   `User` answers "Who can log in?". `PlayerProfile` answers "Which person is this customer?". A customer does not need login credentials or an account to be registered by club staff.
2. **Support players without accounts**:
   Walk-in bookings can immediately anchor to a `PlayerProfile` via phone number with `user = NULL`. When that customer later registers an account, the existing profile can be linked without data migration.
3. **Allow club-specific naming**:
   Clubs retain complete autonomy over player naming and numbering. Club A can call a player "Mo Salah" (#10), while Club B calls the same player "Mohamed" (#7). Neither club overwrites or views the other's labels.
4. **Avoid duplicate customers across clubs**:
   Because `phone_number` is globally unique on `PlayerProfile`, two clubs registering the same phone attach to the same global human identity internally rather than creating fragmented duplicates.

---

## 4. Rejected Alternatives

### 1. `StaffProfile` / `OwnerProfile` / `ManagerProfile` Tables
- **Proposal**: Create dedicated profile models for staff, managers, and owners alongside player profiles.
- **Rejected because**: `ClubMembership` already represents the operational actor relationship, carrying `role` (`OWNER`, `MANAGER`, `STAFF`), court assignments, and manager delegation flags (`manager_can_change_pricing`, `manager_can_settle_transactions`). Adding extra profile tables for internal staff would introduce redundant models and unnecessary join overhead without any product need.

### 2. Global Player Directory Browsing
- **Proposal**: Allow club staff to search and browse all global `PlayerProfile` records in the system.
- **Rejected because**: **Global identity sharing != global visibility**. While `PlayerProfile` is shared internally across clubs to prevent duplicate identity records, clubs must never be able to browse or harvest customers from competing clubs. Visibility is strictly restricted to profiles already linked to the querying club via `ClubPlayer` (`PlayerProfile.objects.filter(club_players__club=current_club)`). Global lookup is only permitted during create/find by explicit phone number.

### 3. Immediate Booking Customer Replacement (Hard Migration)
- **Proposal**: Immediately delete or replace `Booking.customer_name` and `Booking.customer_phone` with mandatory foreign keys to `ClubPlayer`.
- **Rejected because**:
  - **Historical integrity**: A booking's customer name and phone must remain permanent historical snapshots. If a club updates a player's `display_name` a year later, old financial records and historical bookings must continue to reflect the exact name and phone at the time the court was reserved.
  - **Risk mitigation**: Immediate hard migration would disrupt live booking and reservation flows. Booking migration must follow a two-phase approach:
    - **Phase A**: Add optional, nullable `player_profile` and `club_player` foreign keys while preserving snapshot fields forever.
    - **Phase B**: Make `ClubPlayer` the authoritative source of identity for new bookings while keeping snapshot fields immutable.

---

## 5. Consequences

### Positive
- Strict tenant isolation: clubs cannot inspect, modify, or browse each other's player data.
- Greenfield design: introduced in `apps/players/` without modifying active `apps/accounts/`, `apps/clubs/`, or `apps/bookings/` behavior.
- Clean foundation for future self-service player apps (players logging in can query all their bookings across clubs via their `PlayerProfile`).

### Negative / Trade-offs
- Creating a player requires resolving or creating two rows (`PlayerProfile` and `ClubPlayer`). This is encapsulated cleanly inside `apps/players/services.py` within `transaction.atomic()`.
- Booking models will temporarily maintain both foreign keys and snapshot fields during Phase A and Phase B.

---

## 6. References

- [`docs/architecture/security-architecture-v1.md §1`](../security-architecture-v1.md) — Identity Architecture
- [`docs/architecture/security-architecture-v1.md §11`](../security-architecture-v1.md) — Player Architecture
- [`docs/architecture/security-architecture-v1.md §12`](../security-architecture-v1.md) — Migration Strategy
- [`apps/players/AGENTS.md`](../../../apps/players/AGENTS.md) — Players Domain Engineering Guide
