# Settlements — Agent Guide

## Responsibility

`apps/settlements/` owns cash closing, collector custody aggregation, settlement previews, and settlement record creation for recorded transactions.

## Domain Invariants

- **Financial Custody Scope: Club + Collector (NEVER Court)**:
  - Current Custody and Settlement are financially scoped by **CLUB + optional COLLECTOR, NEVER BY COURT**.
  - A collector's custody includes all unsettled transactions collected by that user across all courts in the club.
  - Optional `court` filtering applies only when callers explicitly request court-specific slices (such as court-filtered dashboard metrics).
- **Current Custody Definition**:
  - Authoritative rule: All signed transactions in the selected club where:
    ```text
    is_cancelled = false
    AND settlement_line IS NULL
    ```
  - **All-Time State**: Current Custody is not date-filtered. It includes all unsettled signed transactions regardless of age, payment method, or booking lifecycle status.
  - Includes signed negative `REFUND` rows.
  - Zero-net and negative-net collectors remain visible if they have unsettled candidates. Zero candidates is a distinct state.
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

## Service Layer

- [`apps/settlements/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/services.py):
  - `get_unsettled_transactions_queryset()`: Authoritative candidate query resolver. Scoped by Club + Collector, never Court.
  - `build_custody()`: Calculates financial net, payments, refunds, and earliest candidate date.
  - `preview_custody()`: Read-only preview with authorization guards.
  - `settle_custody()`: Executes settlement in `transaction.atomic()`, locking candidate transactions with `select_for_update()`, creating `Settlement` and `SettlementTransaction` rows, and recording audit logs.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/settlements/`: List and create settlements.
- `/api/v1/clubs/{club_slug}/settlements/preview/`: Read-only preview of unsettled money for a collector (defaults to `request.user`).
- `/api/v1/clubs/{club_slug}/settlements/unsettled-summary/`: Management overview of all collectors with unsettled custody.
- `POST .../settlements/{id}/mark-settled/`: Legacy/test compatibility route for pending settlements.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Centralized via `ClubAccessContext` and [`CanManageClubSettlements`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/permissions.py).
- Staff can preview only their own custody and view only their own settlements.
- Managers without `manager_can_settle_transactions` cannot access management summary or approve settlements.

## Cross-App Dependencies

- Queries and locks [`Transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py) rows.
- Used by [`apps/clubs/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/services.py) to block staff deactivation/deletion when unsettled custody is non-zero.
- Used by [`apps/dashboard/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/services.py) for all-time custody metrics.

## Testing

- Test suite: [`tests/settlements/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/settlements/).
- Key test file: `test_settlement_api.py` (preview, summary, approval, self-approval prevention, locking).
