# Profiles — Agent Guide

`Profile` is a concrete Django model and the single application-role authority.
`User.profile` is a one-to-one reverse relation. Authentication accounts can exist
without an operational profile; a user cannot have two profiles, even disabled ones.

```text
User -- OneToOne --> Profile (user, role, is_enabled, created, modified)
                       ├── OwnerProfile -- clubs M2M --> Club
                       ├── StaffProfile -- club FK --> Club
                       │       └── StaffCourtAccess -- court FK --> Court
                       └── AdminProfile
```

The three children inherit directly from `Profile` using Django multi-table
inheritance. Django creates `profile_ptr`, the child primary key and parent link.
Reverse child names are `owner_profile`, `staff_profile`, and `admin_profile`,
declared through each child's `Meta.default_related_name`. There is no independent
child identity or manually declared composition field.

- `Profile.role` stores only `OWNER`, `STAFF`, or `ADMIN`. `User.role` reads it.
- Shared `clean()` and `save()` live directly on `Profile`. Saving any child runs
  parent validation. Child type/role consistency is a schema check, never an
  authorization decision. Authorization reads the enabled parent's stored role.
- Children do not override `clean()` or `save()` and do not duplicate role fields.
- Create concrete children with `user` and `role`; do not create a parent first
  and attach a child using defaults that could overwrite inherited values.
- `OwnerProfile.clubs` preserves many owners per club and many clubs per owner.
- Staff belong to one club and may have zero or multiple court-access rows.
  Zero assignments grants no court resources. Every assignment must match the
  staff club. Assignment, staff-club, and court-club saves protect this invariant.
- `StaffCourtAccess` is an ordinary association model, not a profile child.
- Admin authority is enabled `Profile.role == ADMIN`; Django admin flags do not
  grant application access. The admin child has no additional data fields.
- Deleting an inherited child deletes its parent and assignments, but not its user.
- Bulk updates bypass model validation. Do not use them for role or scope changes.
- Customer `PlayerProfile` and `ClubPlayer` remain separate from this hierarchy.

The pre-release migration baseline intentionally starts from a recreated database.
`0001_initial.py` represents this final schema directly; there is no supported
forward upgrade from the discarded development migrations.

Run `pytest -n 4 --reuse-db tests/profiles/ tests/authorization/ tests/clubs/`
after profile or authorization changes. Use `--create-db` once after resetting
migration history so reused test databases do not retain the discarded schema.
