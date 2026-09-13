# Clubs — Agent Guide

## Responsibility

`apps/clubs/` owns club tenant definitions, location validation, club membership lifecycle, and hosts the repository's current-state club-scoped access layer (`ClubAccessContext`).

## Locked Identity Role

Per root [`AGENTS.md` §5.1](file:///home/tarek/Desktop/sloty/sloty-backend/AGENTS.md):

- `ClubMembership` is the **club operational actor** (role + court + manager flags). It is not a separate Staff/Owner/Manager profile table and must not be duplicated as one.
- Authentication identity remains `User` (`apps/accounts`).
- Customer identity (`PlayerProfile` / `ClubPlayer`) belongs in `apps/players/` (Phase 1) — not in clubs.

## Domain Invariants

- **Membership Authority**: Authority inside a club is derived exclusively from active `ClubMembership` records.
- **Role Scoping Rules**:
  - `OWNER` and `MANAGER`: Club-wide scope; `court` must be `null`.
  - `STAFF`: Court-scoped; `court` is required and must belong to the same club.
  - Active MANAGER memberships are currently limited to one club per user. Active STAFF memberships are limited to one court assignment per user.
- **Manager Delegation Flags**:
  - `manager_can_settle_transactions` and `manager_can_change_pricing` are valid only for `MANAGER` memberships; they must remain `False` for `OWNER` and `STAFF`.
- **Membership Operational States**:
  1. *Active* (`is_active=True`, `deleted_at=None`): Grants club access.
  2. *Deactivated* (`is_active=False`, `deleted_at=None`): Temporary suspension. Appears in membership lists; can be reactivated via `PATCH`.
  3. *Soft-deleted* (`deleted_at` set, `deleted_by` set): Excluded from current membership/user lists; cannot be reactivated via `PATCH`. Historical bookings/transactions/audit remain intact.
- **Current Custody Settlement Guard**:
  - Deactivating or soft-deleting an operational STAFF membership is blocked with error `MEMBERSHIP_CURRENT_CUSTODY_NOT_SETTLED` (409 Conflict) if that staff user has non-zero Current Custody in the club. Money must be settled before leaving or deactivating.
- **Recreation Prevention**:
  - Re-adding the exact same `(club, user, role, court)` tuple after soft deletion is rejected with `MEMBERSHIP_DELETED_CANNOT_RECREATE`.
- **Location Integrity**:
  - `governorate` and `city` must be valid choices from `apps.common.egypt_locations`, and `city` must strictly belong to the selected `governorate`. Free-text `address` is allowed; `area` has been removed.

## Important Models & Fields

- [`Club`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/models.py):
  - `slug`: Unique URL identifier generated via `slugify` with numeric collision suffixing.
  - `is_active`: Controls club operational status.
- [`ClubMembership`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/models.py):
  - QuerySets: `.current()` excludes soft-deleted; `.granting_access()` filters `is_active=True` and non-deleted.
  - `last_sync_at`: Nullable server timestamp. Read-only to clients. Updated exclusively by the explicit `POST /api/v1/me/sync-heartbeat/` endpoint (`apps/accounts/views.py::SyncHeartbeatAPIView`; see `apps/accounts/AGENTS.md`). Legacy behavior still updates it as a side effect of `ClubScopedAccessMixin` on successful authenticated requests for domains not yet migrated off that mixin (backward-compatible carryover, not a pattern to extend) — domains migrated to the Authorization Spine (`ClubScopedViewMixin` / `SlotyScopedResourceMixin`) never update it implicitly.

## Service Layer

- [`apps/clubs/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/services.py):
  - `create_club_member()`: Creates or attaches `User` and creates `ClubMembership` in an atomic database transaction.
  - `soft_delete_membership()`: Performs soft deletion of a membership with audit trail.
  - `validate_membership_offboarding_current_custody()`: Validates that staff Current Custody is zero before allowing deactivation (enforced in `ClubMembershipSerializer.validate()`) or soft deletion.

## API Boundaries & Key Invariants

- `/api/v1/clubs/`: Platform admins create clubs. Owners/admins can view and update.
- `/api/v1/clubs/{club_slug}/memberships/`:
  - `POST`: Atomically onboard user + membership.
  - `PATCH`: Toggle `is_active` (reactivate/deactivate).
  - `DELETE`: Soft delete membership with audit trail (`MEMBERSHIP_DELETED`).
- `/api/v1/clubs/{club_slug}/users/`: Read-only club users list. Platform admins and owners see all non-deleted memberships. Managers see active managers and staff. Staff cannot list club users.

## Current-State Authorization Engine

> [!WARNING]
> **Legacy / Current-State Notice**: The access layer below is the existing implementation for domains not yet migrated. Target architecture is the Authorization Spine in root [`AGENTS.md` §5](file:///home/tarek/Desktop/sloty/sloty-backend/AGENTS.md) and `apps/common/authorization/`. Do **not** extend this legacy layer to new domains.

Current components in this app:
- [`ClubAccessContext`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/access.py): Central access engine instantiated per request (`from_request(request, club_slug)`).
  - Validates active membership; raises `CLUB_ACCESS_REVOKED` (403) if access was lost.
  - Computes permissions (`is_owner`, `is_manager`, `is_staff`, `manager_can_settle_transactions`, `can_manage_working_hours`, etc.).
  - Supplies scoped querysets: `scoped_courts_queryset()`, `scoped_bookings_queryset()`, `scoped_transactions_queryset()`, `scoped_settlements_queryset()`, `scoped_audit_logs_queryset()`, `scoped_memberships_queryset()`.
- [`ClubScopedAccessMixin`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/mixins.py): Base mixin for club-scoped ViewSets. Attaches access context, injects `club_access` into serializer context, and updates `ClubMembership.last_sync_at` (throttled).
- [`apps/clubs/permissions.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/permissions.py): Thin DRF permission wrappers delegating checks to `ClubAccessContext`.

## Cross-App Dependencies

- Imports [`apps.common.egypt_locations`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/egypt_locations.py) for location choice validation.
- Referenced by virtually all domain apps for scoping and permissions.

## Testing

- Test suite: [`tests/clubs/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/clubs/).
- Key test file: `test_club_api.py` (club setup, onboarding, soft deletion, and access checks).
