# Clubs — Agent Guide

## Responsibility

`apps/clubs/` owns club tenant definitions, location validation, club membership lifecycle, domain authorization rules (`apps/clubs/authorization.py`), and hosts backward-compatibility helpers for the legacy access layer (`ClubAccessContext`).

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
  - `last_sync_at`: Nullable server timestamp. Read-only to clients. Updated exclusively by the explicit `POST /api/v1/me/sync-heartbeat/` endpoint (`apps/accounts/views.py::SyncHeartbeatAPIView`; see `apps/accounts/AGENTS.md`). Authorization requests, including legacy `ClubScopedAccessMixin` requests, never update it implicitly.
  - **Offboarding & Forensic Visibility**: Deactivating or suspending a membership preserves `last_sync_at` intact so owners and managers can inspect the employee's last known connection time (*"آخر اتصال معروف للموظف كان من X ساعات"*). Subsequent heartbeats ignore deactivated memberships.
  - **Live Authorization Revocation**: Deactivating a membership takes immediate effect. The next request to any club-scoped endpoint returns `403 Forbidden` (`CLUB_ACCESS_REVOKED`), completely neutralizing unexpired JWTs without needing token blacklists. Changing roles (e.g. Manager to Staff) takes immediate live effect on permissions and court-scoping.

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

## Authorization Architecture (Spine v2)

The Clubs domain is migrated to the Authorization Spine v2:

- **Resource Configuration**:
  - `Club`: `scopes={"club": {"path": "self"}}`, `default_scope="club"`, `select_related=("created_by",)`.
  - `ClubMembership`: `scopes={"club": {"path": "club"}}`, `default_scope="club"`, `select_related=("club", "user", "court")`.
- **ViewSets**:
  - `ClubMembershipViewSet`: Composes `SlotyScopedResourceMixin` + `SlotyBasePermission` (`authorization_model = ClubMembership`, `authorization_scope = ResourceScope.CLUB`). Object updates evaluate `can_manage_membership_object` (Owners cannot edit or deactivate other Owners; only Platform Admin or self can edit self).
  - `ClubUserListViewSet`: Composes `SlotyScopedResourceMixin` + `SlotyBasePermission` (`authorization_model = ClubMembership`, `authorization_scope = ResourceScope.CLUB`). Overrides `filter_scoped_queryset()` using `apply_club_users_scoping` (Platform Admins and Owners see all non-deleted members; Managers see active Managers and Staff only; Staff are matrix-denied).
  - `ClubViewSet`: Global multi-club list/detail endpoint (`/api/v1/clubs/`). Preserves `scoped_clubs_for_user(request.user)` and `CanManageClubs` evaluating `can_manage_club(user, club, action)`. It does not compose `SlotyScopedResourceMixin` because there is no club in the URL path.
- **Domain Authorization Module** ([`apps/clubs/authorization.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/authorization.py)):
  - Houses all domain business rules: `can_manage_memberships`, `can_create_membership`, `can_manage_membership_object`, `can_list_club_users`, `apply_club_users_scoping`, `can_manage_club`.
- **No Sync Side Effects**:
  - Migrated Clubs endpoints never update `ClubMembership.last_sync_at`. Heartbeats are strictly handled by `POST /api/v1/me/sync-heartbeat/`.

## Legacy Access Layer (Compatibility Only)

> [!NOTE]
> Following Sprint 17 (Authorization Matrix & Compatibility Cleanup):
> Following Sprint 17 (Authorization Matrix & Compatibility Cleanup) and Sprint 19 (Cross-Domain Consistency):
> - `ClubScopedAccessMixin` has been removed from `apps/clubs/mixins.py`. All club-scoped views use `SlotyScopedResourceMixin`.
> - Obsolete permission classes `CanManageClubMemberships` and `CanListClubUsers` have been removed from `apps/clubs/permissions.py`. `CanManageClubs` is retained as the intentional global policy for `/api/v1/clubs/`.
> - Obsolete permission classes `CanManageClubMemberships` and `CanListClubUsers` have been fully removed from `apps/clubs/permissions.py` (Sprint 17 removed their import usage in views; Sprint 19 removed the class bodies, unused imports, and `__all__` entries). `CanManageClubs` is retained as the intentional global policy for `/api/v1/clubs/`.
> - [`ClubAccessContext`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/access.py): Retained strictly for backward compatibility with legacy test fixtures and duck-typed service helpers. All operational endpoints and services resolve access via `RequestAccessContext`.

## Cross-App Dependencies

- Imports [`apps.common.egypt_locations`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/egypt_locations.py) for location choice validation.
- Other domains depend on `Club` / `ClubMembership` as tenant and operational-actor models. Ordinary resource authorization for migrated domains is the Authorization Spine, not this access layer.

## Testing

- Test suite: [`tests/clubs/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/clubs/).
- Key test file: `test_club_api.py` (club setup, onboarding, soft deletion, and access checks).
- Key test files:
  - `test_club_api.py`: Club setup, onboarding, soft deletion, and access checks.
  - `tests/accounts/test_account_membership_lifecycle.py`: Cross-domain integration verifying live membership deactivation/reactivation, role transition enforcement, custody offboarding guards, and delegation flags.
  - `tests/accounts/test_sync_heartbeat_api.py`: Presence verification and offboarding `last_sync_at` retention.
