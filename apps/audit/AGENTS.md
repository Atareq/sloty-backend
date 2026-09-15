# Audit — Agent Guide

## Responsibility

`apps/audit/` owns the append-only activity trail, recording auditable business actions across bookings, transactions, settlements, and memberships.

## Domain Invariants

- **Append-Only & Read-Only**:
  - Audit logs are immutable records. No API create, update, or delete endpoints exist.
  - Audit rows must be created explicitly by business services via [`record_audit_log()`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/services.py). **Never use Django signals.**
- **Stable Machine Action Values**:
  - Machine action values (`BOOKING_CREATED`, `TRANSACTION_CANCELLED`, etc.) are stable API/database keys. Do not translate or rename them.
  - User-facing labels are exposed via `get_action_display()` in `action_label`.
- **Snapshot Architecture**:
  - To prevent N+1 database queries when rendering audit logs, business services use snapshot helpers (`booking_audit_snapshot`, `transaction_audit_snapshot`, `settlement_audit_snapshot`) to store event-time entity facts in JSON (`metadata`, `before_data`, `after_data`).
  - Serializers must consume these stored snapshots rather than querying current live entities.
  - New audit events store ClubPlayer display name/phone as event-time facts. Historical JSON is never rewritten from live ClubPlayer. Phone search on audit list matches `club_player.player_profile.phone_number` to find related bookings.

## Important Models & Fields

- [`AuditLog`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/models.py):
  - `club`, `court` (nullable), `actor` (nullable for system events like hold expiry).
  - `action`: Fixed `Action` choices enum.
  - `entity_type`, `entity_id`: Logical reference to the audited entity.
  - `before_data`, `after_data`, `metadata`: JSON snapshot payloads.
  - `created`: Event timestamp, ordered newest first (`-created, -id`).

## Service Layer

- [`apps/audit/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/audit/services.py):
  - `record_audit_log()`: Central function to create an audit row.
  - `booking_audit_snapshot()`, `transaction_audit_snapshot()`, `settlement_audit_snapshot()`: Extract normalized event-time summaries.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/audit-logs/`: Read-only list with filter parameters (`action`, `entity_type`, `actor`, `court`, `date_from`, `date_to`).
- `/api/v1/clubs/{club_slug}/audit-logs/{id}/`: Read-only detail view.

## Authorization & Scoping (Authorization Spine v2)

Audit is on Authorization Spine **v2** (`SlotyScopedResourceMixin` + `SlotyBasePermission` + `authorization_config`). There is no `apps/audit/authorization.py`: Staff denial is the matrix, and club isolation is the model contract.

```text
Request
    ↓
Authentication
    ↓
RequestAccessContext (club from URL)
    ↓
SlotyBasePermission (ROLE_PERMISSIONS["AuditLogViewSet"] list/retrieve)
    ↓
SlotyScopedResourceMixin.get_queryset()
    AuditLog.authorization_config default_scope="club"
    ↓
DjangoFilterBackend / search / pagination / serializers
```

### Security boundary

- **Club only.** Every `AuditLog` row has a required `club` FK. List and retrieve are scoped to the URL club before filters, search, or pagination.
- **Not court-scoped.** `AuditLog.court` is nullable (membership deletes, some settlements). Do not apply Booking/Transaction `CLUB + COURT` to audit rows — that would hide null-court events.
- **Not collector-scoped.** Do not apply Settlement collector narrowing to audit rows.
- **Not actor-scoped.** Owners see all actors in the club, including Staff actions.
- **WHO:** Platform Admin and Owner may `list` and `retrieve`. Staff are matrix-denied (HTTP 403), including their own actions.
- **Out-of-club IDs:** omitted from the scoped queryset → HTTP 404. Other-club members hitting this club's URL remain HTTP 403.
- Search (phone / entity / actor / court filters) may only narrow the already-authorized queryset.
- Event creation (`record_audit_log()`) is unchanged. This migration does not rewrite historical rows, snapshots, payloads, timestamps, or actors.
- `CanViewClubAuditLogs` was deleted. Audit ViewSets use the Spine only (no implicit `last_sync_at` updates).

## Cross-App Dependencies

- Called by service layers in `apps.bookings`, `apps.transactions`, `apps.settlements`, and `apps.clubs`.

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/audit/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/audit/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/audit/).
- Key test files:
  - `test_audit_api.py` (immutability, filtering, snapshot rendering, API contract).
  - `test_audit_authorization.py` (Spine v2 club-only contract, Staff 403, club/search/pagination isolation, historical-row immutability).
