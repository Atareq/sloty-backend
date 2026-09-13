# Accounts — Agent Guide

## Responsibility

`apps/accounts/` owns user authentication, identity records, platform administration authority, and account-level diagnostics. It is decoupled from club-scoped business domains.

## Domain Invariants

- **Identity Only**: The `User` model represents identity and global platform authority only. It must never store club, court, or business roles (such as `OWNER`, `MANAGER`, or `STAFF`).
- **No Orphan Business Users**: Active non-platform users must not be created via generic user endpoints. Club business users must be created through club-scoped membership onboarding (`apps/clubs/services.py`) so `User` and `ClubMembership` are persisted atomically.
- **Creator Tracking**: `User.created_by` stores the user account creator, not club membership creators.

## Important Models & Fields

- [`User`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/models.py):
  - `is_platform_admin`: Boolean flag granting platform super-admin privileges. `is_platform_super_admin()` helper returns this flag.
  - `phone_number`: Standardized phone number field via `phonenumber_field`.
  - `created_by`: Self-referential nullable foreign key tracking which admin created this user account.
  - Standard Django `is_staff` / `is_superuser` are not exposed in read responses or accepted in user creation payloads.

## Service Layer

- [`apps/accounts/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/services.py):
  - `find_orphan_business_users()`: Diagnostic query returning active non-platform users who have no active access-granting club memberships.

## API Boundaries & Key Invariants

- **Authentication Endpoints**:
  - `/api/v1/auth/token/`: JWT token obtain endpoint (`SlotyTokenObtainPairView`). Accepts optional `club_slug` to return convenience JWT claims (`role`, `club_id`, `court_id`).
  - `/api/v1/auth/token/refresh/`: JWT token refresh endpoint.
  - Auth failure codes: Expired tokens raise `SESSION_EXPIRED`; inactive accounts return `USER_INACTIVE`; deleted accounts return `USER_DELETED`.
- **User Profile**:
  - `GET /api/v1/me/`: Returns current authenticated user profile, creator details (`account_created_by`), and active memberships. Must prefetch memberships with `to_attr="active_memberships_for_me"` selecting club and court to avoid per-membership queries.
- **Sync Heartbeat**:
  - `POST /api/v1/me/sync-heartbeat/` (`SyncHeartbeatAPIView`): Authenticated-only, no request payload. Updates `last_sync_at` (server `timezone.now()`, never client-supplied) on **all** of the authenticated user's active, access-granting `ClubMembership` rows (`ClubMembership.objects.granting_access().filter(user=request.user)`), and returns `{"last_sync_at": ...}`. This is the **only** mechanism that updates `last_sync_at`. Authorization (authentication, role authority, club/resource scope) and sync/presence tracking are separate concerns by design: neither `ClubScopedViewMixin` nor `SlotyScopedResourceMixin` (Authorization Spine, `apps/common/authorization/`) update `last_sync_at` as an implicit response side effect. The legacy `ClubScopedAccessMixin` (`apps/clubs/mixins.py`) still does this for domains not yet migrated off it, purely as a backward-compatible carryover — do not extend that side effect to new code or migrate it into the Spine.
- **Platform User Management**:
  - `/api/v1/users/`: Restricted to Platform Super Admins for full CRUD. Club Owners have read-only access to staff within their club scope. Creating active business users here is prohibited.
- Serializers and schemas define the authoritative request/response shape (e.g., [`UserMeSerializer`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/serializers.py), [`UserListSerializer`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/serializers.py)).

## Authorization & Scoping (Current-State)

> [!NOTE]
> This represents current-state/legacy authorization implementation. Do not treat it as an unchangeable target architecture.

- [`IsPlatformSuperAdmin`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/permissions.py): Grants access if `user.is_platform_super_admin()`.
- [`CanAccessUsers`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/permissions.py): Platform super admin has full access; Club Owners have safe-method access scoped to staff memberships in their owned clubs.

## Cross-App Dependencies

- Imports [`ClubMembership`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/models.py) to resolve active memberships for `/api/v1/me/` and scoped owner permissions.

## Testing

- Test suite: [`tests/accounts/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/accounts/).
- Key test files:
  - `test_user_model.py`: Identity constraints and platform admin checks.
  - `test_account_api.py`: `/api/v1/me/` and `/api/v1/users/` permissions and serialization.
  - `test_sync_heartbeat_api.py`: `/api/v1/me/sync-heartbeat/` contract, backend-authoritative timestamp, multi-membership fan-out, user isolation, and the regression guard that ordinary API traffic (e.g. Courts) never updates `last_sync_at` implicitly.
  - `test_seed_demo_data.py`: Multi-club demo seed data creation.
