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

Settlement is on Authorization Spine **v1** (`ClubScopedViewMixin` + `RequestAccessContext` + `SlotyBasePermission`) with domain business rules in `apps/settlements/authorization.py`.

```text
RequestAccessContext (Role, Club, Admin; court not used for custody)
         ↓
SlotyBasePermission (Role + SettlementViewSet + DRF Action)
         ↓
ViewSet & Object Permission (can_access_settlement)
         ↓
Serializer / Domain Authorization Layer (apps/settlements/authorization.py)
         ↓
Pure Financial Custody Services (apps/settlements/services.py)
```

### Locked Scope Direction (v2 queryset target)

Per root [`AGENTS.md` §5](file:///home/tarek/Desktop/sloty/sloty-backend/AGENTS.md):

- Financial custody scope is **Club + Collector**, **never Court**.
- When Settlement querysets move to Spine v2, use:

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
    },
    "default_scope": "club",
    "select_related": (),
    "prefetch_related": (),
}
```

- Collector filtering stays as **domain business narrowing** in `authorization.py` / view filters — not a Court scope and not a second security engine.
- URLs remain club-scoped: `/api/v1/clubs/{club_slug}/settlements/` (do not inject court into settlement URLs).

### Authorization Boundary (`apps/settlements/authorization.py`)

Allowed here: true business invariants (self-approval bans, manager settle flags, collector gates). Forbidden: re-implementing club isolation the spine already owns.

User authority is evaluated at the API/domain boundary *before* calling pure financial services:
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
- Used by [`apps/clubs/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/services.py) to block staff deactivation/deletion when unsettled custody is non-zero.
- Used by [`apps/dashboard/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/services.py) for all-time custody metrics.

## Testing

- Test suite: [`tests/settlements/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/settlements/).
- Key test file: `test_settlement_api.py` (preview, summary, approval, self-approval prevention, locking, current custody scope).
