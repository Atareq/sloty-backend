# Accounts — Agent Guide

## Responsibility

`apps/accounts/` owns **authentication identity** (`User`), platform administration authority, auth token endpoints, and account-level diagnostics. It is decoupled from club-scoped business domains and from customer/player identity.

## Locked Identity Boundary

Per root [`AGENTS.md` §5.1](file:///home/tarek/Desktop/sloty/sloty-backend/AGENTS.md):

```text
User                          ← this app (authentication identity)
 ├── Profile                   ← apps/profiles (application role authority)
 └── PlayerProfile → ClubPlayer ← apps/players Phase 1 (customer identity)
```

- `User` is credentials + platform authority only.
- Do **not** add club roles, court assignments, player/booking, or financial fields to `User`.
- `Profile`, `OwnerProfile`, `StaffProfile`, and `AdminProfile` belong to `apps/profiles/`; accounts must read them rather than duplicate role fields.
- Customer identity (`PlayerProfile` / `ClubPlayer`) belongs in `apps/players/` (Phase 1), not in accounts.

## Domain Invariants

- **Identity Only**: The `User` model represents authentication identity only. It must never store club, court, or application-role data; `Profile.role` is authoritative.
- **Profile Boundary**: Inherited Profile children define application scope: OwnerProfile → clubs, StaffProfile → club + court-access rows, AdminProfile → platform-wide role marker.
- **Creator Tracking**: `User.created_by` stores the user account creator, not club membership creators.

## Important Models & Fields

- [`User`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/models.py):
  - Platform admin authority is `Profile.role == ADMIN` via `is_platform_super_admin()`.
  - `phone_number`: Standardized phone number field via `phonenumber_field` (optional on `User`; **not** the player identity key — that is `PlayerProfile.phone_number` UNIQUE).
  - `created_by`: Self-referential nullable foreign key tracking which admin created this user account.
  - Standard Django `is_staff` / `is_superuser` are not exposed in read responses or accepted in user creation payloads.

## Service Layer

- [`apps/accounts/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/services.py):
  - `find_orphan_business_users()`: Diagnostic query returning active users with no Profile or a disabled Profile.

## API Boundaries & Key Invariants

### Authentication (Locked — §5.2)

Authentication answers **only** “Who are you?”. It must not decide club/court/resource access.

- `/api/v1/auth/token/`: JWT token obtain endpoint (`SlotyTokenObtainPairView`). Accepts optional `club_slug` to return **convenience** JWT claims (`role`, `club_id`, `court_id`).
- `/api/v1/auth/token/refresh/`: JWT token refresh endpoint. It rechecks the current user record before issuing access, so inactive, deleted, and password-changed accounts cannot refresh.
- `/api/v1/auth/password/change/`: authenticated own-password endpoint. It accepts `current_password`, `new_password`, and `new_password_confirmation`, applies Django validators, and returns `204 No Content`.
- Auth failure codes: expired access tokens raise `SESSION_EXPIRED`; malformed/expired refresh tokens use `TOKEN_NOT_VALID`; inactive accounts return `USER_INACTIVE`; deleted accounts return `USER_DELETED`; password-hash mismatches return `PASSWORD_CHANGED`.

**JWT rule (locked):** Token claims are UX convenience only. They are never authority. Authority is:

```text
Request URL → resolve_club_scope / resolve_global_scope → RequestAccessContext → Authorization Spine
```

Frontend must never treat “JWT contains `club_id`” as proof of access.

**Password lifecycle:**
- Tokens include SimpleJWT's signed password-hash digest and are revalidated against the current database password hash. A password change immediately invalidates previously issued access and refresh tokens; clients must sign in again.
- The strict initial rollout also rejects claim-less tokens issued before this setting was enabled.
- No admin can change another account's password through this endpoint. Password reset (email/SMS/OTP) is a future product phase and is not implemented.
- Login retains the generic credential failure response for unknown, incorrect-password, and inactive accounts to avoid account enumeration.

### Current Authenticated Profile

- `GET /api/v1/me/`: Returns current authenticated user identity, `profile_role`, and creator details (`account_created_by`).

### Platform User Management

- `/api/v1/users/`: Restricted to Platform Super Admins for full CRUD. Club Owners have read-only access to staff within their club scope. Creation produces an AdminProfile inheriting Profile; staff creation belongs to the club-scoped staff API.
- Serializers and schemas define the authoritative request/response shape (e.g., [`UserMeSerializer`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/serializers.py), [`UserListSerializer`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/serializers.py)).

## Authorization & Scoping (Current-State)

> [!NOTE]
> This represents current-state/legacy authorization implementation for the users API. Do not treat it as an unchangeable target architecture. Cross-club operational authorization uses the Authorization Spine under `apps/common/authorization/` (see root AGENTS.md §5).

- [`IsPlatformSuperAdmin`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/permissions.py): Grants access if `user.is_platform_super_admin()`.
- [`CanAccessUsers`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/accounts/permissions.py): Platform super admin has full access; Club Owners have safe-method access scoped to enabled staff profiles in their owned clubs.

## Cross-App Dependencies

- Reads [`Profile`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/profiles/models.py) for `/api/v1/me/`, token claims, and scoped owner permissions.
- Does not own `PlayerProfile` / `ClubPlayer` (Phase 1 → `apps/players/`).
- `PlayerProfile` / `ClubPlayer` live in `apps/players/`. See [`apps/players/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/AGENTS.md) and [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md) for the customer identity boundary and phase migration notes.
- `PlayerProfile` / `ClubPlayer` customer identity lives in `apps/players/`. See [`apps/players/AGENTS.md`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/players/AGENTS.md), [`docs/architecture/security-architecture-v1.md §11`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md), and [`ADR-001`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/adr/ADR-001-player-identity-and-booking-link.md).

## Testing

- Test suite: [`tests/accounts/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/accounts/).
- Key test files:
  - `test_user_model.py`: Identity constraints and platform admin checks.
  - `test_account_api.py`: `/api/v1/me/` and `/api/v1/users/` permissions and serialization.
  - Profile architecture tests verify current role authority, Profile extension scope, retired-manager deactivation, and profile-backed seed data.
  - `test_seed_demo_data.py`: Multi-club demo seed data creation.

## Concrete Profile identity

`User.profile` is a one-to-one reverse relation. `User.role` reads its stored role
(or None without a profile); access checks additionally require `is_enabled`.
Create a concrete Profile child directly. User and profile creation is atomic.
Use `select_related("profile")`, not a plural profile prefetch.
