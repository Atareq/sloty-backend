# Profiles — Agent Guide

## Responsibility

`apps/profiles/` owns the application identity layer. `User` remains the
authentication account; `Profile` owns the single current application role.

```text
User -> Profile.role
       OWNER -> OwnerProfile -> Clubs (many-to-many)
       STAFF -> StaffProfile -> Court
       ADMIN -> AdminProfile
```

`Profile`, `PlayerProfile`, and `ClubPlayer` are distinct models. This app
must never own customer identity, booking identity, financial records, or
resource business workflows.

## Invariants

- Supported application roles are only `OWNER`, `STAFF`, and `ADMIN`.
- `Profile.role` is the sole role authority. Role extension models carry no
  duplicate role field.
- Each extension is one-to-one with its base `Profile` and is valid only for
  its matching role.
- Owners may own multiple clubs and clubs may have multiple owners.
- A staff profile has one assigned court; its club is derived through that
  court.
- `AdminProfile` is descriptive only. Platform-wide authority derives from
  `Profile.role == ADMIN`.
- No Manager role, ManagerProfile, or membership authorization model exists.

## Testing

Run `pytest -n 4 --reuse-db tests/profiles/ tests/authorization/` after
profile or authorization changes.
