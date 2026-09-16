# Settlements — Agent Guide

## Responsibility

`apps/settlements/` owns cash closing, collector custody aggregation, settlement previews, and settlement record creation for recorded transactions.

### Core Principle: SIMPLICITY WINS

Keep the implementation direct, minimal, and clean.
- **No synthetic status lifecycle**: Settlements are created immediately settled; there is no `status` (`PENDING`/`SETTLED`) field.
- **Single actor field**: `Settlement.settled_by` is the single read-only actor field set internally from `request.user` (never client-controlled, never `collected_by` or `created_by` on the model).
- **Service boundary**: Services expose only `preview(...)` and `create(...)`.
- **Custody scope**: Financial custody is strictly Club + Collector/User, never restricted by Court.

---

## Domain Invariants

1. **Financial Custody Scope: Club + Collector (NEVER Court)**:
   - Current Custody and Settlement are financially scoped strictly by **CLUB + optional COLLECTOR, NEVER BY COURT**.
   - A collector's custody includes all unsettled transactions collected by that user across all courts in the club.
   - Court is an operational booking/scheduling construct and is never part of financial custody.
2. **Current Custody Definition**:
   - Authoritative rule: All signed transactions in the selected club where:
     ```text
     is_cancelled = false
     AND settlement_line IS NULL
     ```
   - **All-Time State**: Current Custody is not date-filtered. It includes all unsettled signed transactions regardless of age, payment method, or booking lifecycle status.
   - Includes signed negative `REFUND` rows.
   - Zero-net and negative-net collectors remain visible if they have unsettled candidates. Zero candidates is a distinct state.
3. **Mathematical Parity Between Preview and Create**:
   - Preview (`preview`) and execution (`create`) consume the exact same underlying candidate calculation (`_calculate`).
   - Guarantee: Whatever transactions are shown in preview are the exact rows locked and settled by execution.
4. **Role & Actor Model**:
   - `settled_by`: The administrator or owner approving and executing the settlement (read-only, set from `request.user`).
   - `user`: The collector whose collected money is being settled.
5. **Self-Approval Invariant**:
   - Staff must **never** approve their own settlement.
   - Platform Admins and Club Owners are permitted to approve their own collected funds.
6. **One-Time Settlement Enforcement**:
   - [`SettlementTransaction.transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py) is a `OneToOneField` to `Transaction`. A transaction can never be linked to more than one settlement.
7. **Preview Immutability**:
   - Preview endpoints are strictly read-only and side-effect-free (no DB locks, no state mutations).
8. **Timestamp Semantics (`created` vs. `occurred_at`)**:
   - `Transaction.created` (server persistence time) is authoritative for candidate ordering (`order_by("created", "id")`), transaction date filtering, and determining settlement `period_start` (the `created` timestamp of the earliest candidate) and `period_end` (the `created` timestamp of the latest candidate).
   - `Transaction.occurred_at` records when the event took place in the physical world (supporting offline sync) and must never override `created` for financial ledger ordering or custody boundaries.

---

## Important Models & Fields

- [`Settlement`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py):
  - `club`, `court` (nullable filter/display only), `total_amount`, `notes`, `settled_by`, `created_at`.
  - Derived properties: `transaction_count`, `period_start`, `period_end`.
- [`SettlementTransaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/settlements/models.py):
  - Pivot table binding `settlement` and `transaction` (`OneToOneField`).

---

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
Domain authority (preview / create / summary)
    ↓
Pure financial services (apps/settlements/services.py)
```

### Security boundary

- Financial custody and persisted Settlements: **Club + optional Collector**, **never Court**.
- `Settlement.authorization_config` declares only `club` (`default_scope="club"`). `Settlement.court` is an optional display/filter field, not a Spine scope.
- Collector visibility cannot be a Spine `ResourceScope`: it depends on the current Profile role and collector identity. `apply_collector_scope()` therefore narrows the already club-scoped queryset.
- Operational Transactions remain Club + Court. A collector may have custody of collections from courts they are not assigned to.
- URLs remain club-scoped: `/api/v1/clubs/{club_slug}/settlements/` (do not inject court into settlement URLs).

### Authorization Boundary (`apps/settlements/authorization.py`)

Allowed here: true business invariants (self-approval bans and collector gates). Forbidden: re-implementing club isolation the spine already owns.

- `apply_collector_scope(context, queryset)`
- `validate_preview_authority(*, context, collector)`
- `validate_settlement_authority(*, context, collector)`
- `validate_unsettled_summary_authority(*, context, collector=None)`
- `can_manage_settlements(context)`
- `can_access_settlement(context, settlement)`
- `can_approve_collector(context, collector_id, roles=None)`

### Pure Financial Custody Services (`apps/settlements/services.py`)

Core financial functions have clean signatures:
- `preview(club, user=None, court=None) -> dict`
- `create(club, user=None, *, settled_by, notes="", court=None) -> Settlement`

---

## API Boundaries & Key Invariants

- `GET /api/v1/clubs/{club_slug}/settlements/`: List settlements.
- `POST /api/v1/clubs/{club_slug}/settlements/`: Create settlement.
- `GET /api/v1/clubs/{club_slug}/settlements/{id}/`: Retrieve settlement details.
- `GET /api/v1/clubs/{club_slug}/settlements/preview/`: Read-only preview of unsettled money for a collector.
- `GET /api/v1/clubs/{club_slug}/settlements/unsettled-summary/`: Management overview of all collectors with unsettled custody.

---

## Cross-App Dependencies

- Queries and locks [`Transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py) rows.
- Preview and settlement-line customer fields are sourced from `Booking.club_player` via `apps.bookings.identity`. Response keys stay `booking_customer_name` / `booking_customer_phone`.
- Used by [`apps/clubs/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/services.py) to block staff deactivation/deletion when unsettled custody is non-zero.
- Used by [`apps/dashboard/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/services.py) for all-time custody metrics.

---

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/settlements/`.
- Test suite: [`tests/settlements/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/settlements/).
- Key test files:
  - `test_settlement_api.py`: preview, summary, approval, self-approval prevention, locking, current custody scope.
  - `test_settlement_authorization.py`: Spine v2 club-only contract, collector isolation, court independence.
