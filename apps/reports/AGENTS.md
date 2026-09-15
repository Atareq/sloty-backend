# Reports — Agent Guide

## Responsibility

`apps/reports/` owns cross-domain analytical reporting. It currently implements the Court Usage Report. It contains no database models or migrations.

## Domain Invariants

- **Maximum Date Range**:
    - Report date ranges are inclusive and capped at 31 calendar days (`REPORT_MAX_RANGE_DAYS = 31` in [`apps/reports/constants.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/constants.py)).
- **Usage Status Inclusion**:
    - Default usage statuses: `CONFIRMED`, `COMPLETED`, `NO_SHOW`.
    - `HOLD` is included only when explicitly requested.
    - `CANCELLED` and `EXPIRED` bookings are strictly excluded from usage analysis.
- **Occupancy Clipping**:
    - Bookings overlapping report period boundaries have their occupied minutes clipped to the selected window (e.g. daytime vs evening).
- **Financial Authority**:
    - Financial values use historical `Booking.total_price` snapshots, **not** current court pricing periods.
    - Financial totals sum non-cancelled attached transactions.
    - Booking revenue is attributed entirely to the booking's local start date, even if occupancy spans past midnight.
- **Customer identity**: Court Usage Report does **not** display `customer_name` / `customer_phone` or ClubPlayer. It is occupancy and financial aggregation only. Tests create bookings through ClubPlayer resolution; that is fixture data, not a report identity contract.
- **Demand Analysis Buckets**:
    - Demand is analyzed in 60-minute clock buckets (`DEMAND_BUCKET_MINUTES = 60`). Low-demand results must include zero-demand generated slots.

## Service Layer

- [`apps/reports/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/services.py):
    - `get_court_usage_report()`: Coordinates date window validation, occupancy clipping, demand bucketing, and financial aggregation on **already authorized** court and booking querysets.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/reports/court-usage/`:
    - Query parameters: `date_from`, `date_to`, `court`, `period` (`all_day`, `daytime`, `evening`, `custom`), `hour_from`, `hour_to`, `staff`, `status`.
- No Excel / CSV / PDF export path exists. The JSON response is the only output.

## Authorization & Scoping (Authorization Spine)

Reports is a **read model**. There is no Report table, so the Court Usage endpoint composes `ClubScopedViewMixin` + `SlotyBasePermission` (`permission_view_name="CourtUsageReportViewSet"`, `action="list"`). It does **not** use `SlotyScopedResourceMixin`.

```text
Request
    ↓
Authentication
    ↓
RequestAccessContext (club from URL)
    ↓
SlotyBasePermission (ROLE_PERMISSIONS["CourtUsageReportViewSet"] list)
    ↓
Authorized source querysets (Court / Booking authorization_config, COURT)
    ↓
Report filters (date, period, optional court, optional created_by staff, status)
    ↓
Aggregation / JSON response
```

### Report-by-report security boundary

| Report | Data sources | Scope | Roles | Export |
| :--- | :--- | :--- | :--- | :--- |
| Court Usage | Courts, Bookings, paid amounts annotated from those bookings' transactions | Club + Court (Admin/Owner see all club courts). Not creator-scoped. Not collector-scoped. No Settlement/custody rows. | Platform Admin / Owner. Staff **403**. | None (JSON only, same querysets) |

- Optional `court=` may only **narrow** the authorized court queryset. Another club's court → HTTP 403.
- Optional `staff=` is a `Booking.created_by` report filter. The named user must have active access in this club (`REPORT_STAFF_NOT_IN_CLUB`); it is **not** Settlement collector isolation.
- Do not apply Transaction Staff `created_by` narrowing or Settlement collector narrowing to report totals. Report viewers are financial roles.
- [`apps/reports/authorization.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/authorization.py) exists only for: Spine source querysets, court-filter 403, and the staff-membership filter rule.
- Report views use Spine source querysets. They do not use `ClubAccessContext` or a Clubs permission class (no implicit `last_sync_at` updates).

## Cross-App Dependencies

- Queries `apps.bookings`, `apps.courts`, `apps.transactions`, and `apps.accounts`.

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db apps/reports/tests/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`apps/reports/tests/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/tests/).
- Key test files:
    - `test_court_usage_report.py` (clipping, multi-day ranges, demand buckets, role permissions, query bounds).
    - `test_report_authorization.py` (Spine composition, club/court/staff isolation, aggregation isolation).
