# Settlements — Agent Guide

## Responsibility

`apps/settlements/` owns cash closing, collector custody aggregation, settlement previews, and settlement record creation for recorded transactions.

## Domain Invariants

- **Financial Custody Scope: Club + Collector (NEVER Court)**:
  - Current Custody and Settlement are financially scoped strictly by **CLUB + optional COLLECTOR, NEVER BY COURT**.
  - A collector's custody includes all unsettled transactions collected by that user across all courts in the club.
  - Court is an operational booking/scheduling construct and is never part of financial custody.
- **Current Custody Definition**:
  - Authoritative rule: All signed transactions in the selected club where:
    ```text
    is_cancelled = false
    AND settlement_line IS NULL
    ```
  - **All-Time State**: Current Custody is not date-filtered. It includes all unsettled signed transactions regardless of age, payment method, or booking lifecycle status.
  - Includes signed negative `REFUND` rows.
  - Zero-net and negative-net collectors remain visible if they have unsettled candidates. Zero candidates is a distinct state.
- **Mathematical Parity Between Preview and Settle**:
  - Preview (`preview_custody` / `build_custody`) and execution (`settle_custody`) consume the exact same underlying candidate query (`get_unsettled_transactions_queryset`).
  - Guarantee: Whatever transactions are shown in preview are the exact rows locked and settled by execution.
- **Role & Actor Naming**:
  - `collected_by`: The staff member/user whose collected money is being settled.
  - `created_by` / `settled_by`: The administrator/manager approving and executing the settlement.
  - Do not use `created_by` to mean the collector.
- **Self-Approval Invariant**:
  - Managers and staff must **never** approve their own settlement.
  - Platform Admins and Club Owners are permitted to approve their own collected funds.
- **Manager Authority Gates**:
  - Managers require `manager_can_settle_transactions=True` on their `ClubMembership` to view unsettled summaries or approve settlements.
  - Managers can settle only Staff and other Managers; they cannot settle Owner money.
- **One-Time Settlement Enforcement**:
  - [`SettlementTransaction.transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py) is a `OneToOneField` to `Transaction`. A transaction can never be linked to more than one settlement.
- **Direct Settled State**:
  - New API-approved settlements are created directly with status `SETTLED` (`settled_by` and `settled_at` set immediately). Status `PENDING` exists only for legacy records and manual test data.
  - Preview endpoints are strictly read-only and side-effect-free (no DB locks, no state mutations).
- **Timestamp Semantics (`created` vs. `occurred_at`)**:
  - `Transaction.created` (server persistence time) is authoritative for candidate ordering (`order_by("created", "id")`), transaction date filtering, and determining settlement `period_start` (the `created` timestamp of the earliest candidate) and `period_end` (settlement execution time).
  - `Transaction.occurred_at` records when the event took place in the physical world (supporting offline sync) and must never override `created` for financial ledger ordering or custody boundaries.

## Important Models & Fields

- [`Settlement`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py):
  - `club`, `court` (nullable), `period_start`, `period_end`, `total_amount`, `transaction_count`.
  - `status`: `PENDING`, `SETTLED`.
  - `collected_by`, `created_by`, `settled_by`, `settled_at`.
- [`SettlementTransaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py):
  - Pivot table binding `settlement` and `transaction` (`OneToOneField`).

## Architecture & Authorization Spine Integration

Settlement is on Authorization Spine **v2** (`SlotyScopedResourceMixin` + `SlotyBasePermission` + `authorization_config`) with collector/settle-authority rules in `apps/settlements/authorization.py`.

```text
Request
    ↓
Authentication
    ↓
RequestAccessContext (club from URL; court is not a custody fact)
    ↓
SlotyBasePermission (ROLE_PERMISSIONS["SettlementViewSet"])
    ↓
SlotyScopedResourceMixin.get_queryset()
    Settlement.authorization_config default_scope="club"
    ↓
filter_scoped_queryset()  # collector narrowing when not can_manage_settlements
    ↓
DjangoFilterBackend / pagination / serializers
    ↓
Domain authority (preview / create / summary / mark-settled)
    ↓
Pure financial services (apps/settlements/services.py)
```

### Security boundary

- Financial custody and persisted Settlements: **Club + optional Collector**, **never Court**.
- `Settlement.authorization_config` declares only `club` (`default_scope="club"`). `Settlement.court` is an optional display/filter field, not a Spine scope.
- Collector visibility cannot be a Spine `ResourceScope`: it depends on role plus `manager_can_settle_transactions`. `apply_collector_scope()` therefore narrows the already club-scoped queryset.
- Operational Transactions remain Club + Court. A collector may have custody of collections from courts they are not assigned to.
- URLs remain club-scoped: `/api/v1/clubs/{club_slug}/settlements/` (do not inject court into settlement URLs).
- Settlement ViewSets use the Spine plus `apps/settlements/authorization.py`. They do not call `ClubAccessContext` querysets. Internal service wrappers (`create_approved_settlement` and unused preview helpers) still duck-type ClubAccessContext-shaped `can_*` methods for concurrency tests.

### Authorization Boundary (`apps/settlements/authorization.py`)

Allowed here: true business invariants (self-approval bans, manager settle flags, collector gates). Forbidden: re-implementing club isolation the spine already owns.

User authority is evaluated at the API/domain boundary *before* calling pure financial services:
- `apply_collector_scope(context, queryset)`
- `validate_preview_authority(*, context, collector)`
- `validate_settlement_authority(*, context, collector)`
- `validate_unsettled_summary_authority(*, context, collector=None)`
- `can_manage_settlements(context)`
- `can_access_settlement(context, settlement)`
- `can_approve_collector(context, collector_id, roles=None)`

### Pure Financial Custody Services (`apps/settlements/services.py`)
Core financial functions have clean, unencumbered signatures with **zero knowledge of authorization**:
- `get_unsettled_transactions_queryset(*, club, collector=None, lock=False)`
- `build_custody(*, club, collector=None, period_end=None)`
- `settle_custody(*, club, collector, actor, notes="")`
- `mark_settlement_settled(*, settlement, actor)`

**Invariant**: Never pass `context`, `access`, `court`, `request`, `role`, or `permission` into these pure financial functions.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/settlements/`: List and create settlements.
- `/api/v1/clubs/{club_slug}/settlements/preview/`: Read-only preview of unsettled money for a collector (defaults to `request.user`).
- `/api/v1/clubs/{club_slug}/settlements/unsettled-summary/`: Management overview of all collectors with unsettled custody.
- `POST .../settlements/{id}/mark-settled/`: Transition pending settlements to settled.

## Cross-App Dependencies

- Queries and locks [`Transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py) rows.
- Preview and settlement-line customer fields are sourced from `Booking.club_player` (exact version) via `apps.bookings.identity`. Response keys stay `booking_customer_name` / `booking_customer_phone`.
- Used by [`apps/clubs/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/services.py) to block staff deactivation/deletion when unsettled custody is non-zero.
- Used by [`apps/dashboard/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/services.py) for all-time custody metrics.

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/settlements/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/settlements/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/settlements/).
- Key test files:
  - `test_settlement_api.py`: preview, summary, approval, self-approval prevention, locking, current custody scope.
  - `test_settlement_authorization.py`: Spine v2 club-only contract, collector isolation, court independence, filter leak, custody vs Transaction API.
