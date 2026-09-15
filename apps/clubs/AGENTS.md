# Clubs — Agent Guide

## Responsibility

`apps/clubs/` owns the `Club` tenant model, location validation, and the
global `/api/v1/clubs/` endpoint. It does not own operational roles or a
membership model.

## Active Identity and Scope

Application authorization is profile-backed:

```text
User -> Profile.role
OWNER -> OwnerProfile.clubs
STAFF -> StaffProfile.court -> Court.club
ADMIN -> AdminProfile
```

- `Profile.role` is the only application-role authority.
- Owner access to a club is derived from `OwnerProfile.clubs` (many-to-many).
- Staff access is derived from the one assigned `StaffProfile.court`.
- Admin access is platform-wide because `Profile.role == ADMIN`; `AdminProfile`
  is an extension record, not a separate permission engine.
- No Manager, ManagerProfile, delegation flag, or `ClubMembership` model is
  part of current runtime behavior.

## Domain Invariants

- Club URLs remain the authoritative tenant boundary.
- `governorate` and `city` use `apps.common.egypt_locations`; city must belong
  to the selected governorate.
- `slug` is unique and generated with a numeric collision suffix where needed.
- A missing, malformed, or out-of-scope Profile must be denied with
  `CLUB_ACCESS_REVOKED`; JWT claims never override current database state.

## API Boundary

- `/api/v1/clubs/`: admins may create; admins and owners may update; list and
  detail are scoped by the current Profile.
- Membership-management and club-user-list endpoints were retired with the
  `ClubMembership` model. Do not recreate them around another membership
  abstraction without an approved API design.

## Authorization

`scoped_clubs_for_user()` and `CanManageClubs` consume the Profile graph.
Club-scoped operational endpoints use the shared Authorization Spine;
`apps/clubs/authorization.py` contains only club-specific predicates.

`ClubAccessContext` exists solely as a duck-typed fixture for direct legacy
service tests. Views, resolvers, and runtime authorization use
`RequestAccessContext`, never this compatibility object.

## Cross-App Boundaries

- `apps/profiles` owns Profile models and extension validation.
- `apps/courts` owns courts and StaffProfile's assigned court target.
- `apps/players` owns customer identity (`PlayerProfile`, `ClubPlayer`), which
  is unrelated to operational staff/owner/admin profiles.
