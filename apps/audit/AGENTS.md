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

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Scoped via `ClubAccessContext` and [`CanViewClubAuditLogs`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/permissions.py).
- Platform Admins, Owners, and Managers can list and retrieve audit logs.
- Staff users are denied access to audit logs.

## Cross-App Dependencies

- Called by service layers in `apps.bookings`, `apps.transactions`, `apps.settlements`, and `apps.clubs`.

## Testing

- Test suite: [`tests/audit/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/audit/).
- Key test file: `test_audit_api.py` (immutability, filtering, snapshot rendering, access scoping).
