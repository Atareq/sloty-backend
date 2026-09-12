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
- **Demand Analysis Buckets**:
  - Demand is analyzed in 60-minute clock buckets (`DEMAND_BUCKET_MINUTES = 60`). Low-demand results must include zero-demand generated slots.

## Service Layer

- [`apps/reports/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/services.py):
  - `build_court_usage_report()`: Coordinates date window validation, booking query scoping, occupancy clipping, demand bucketing, and financial aggregation.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/reports/court-usage/`:
  - Query parameters: `date_from`, `date_to`, `court`, `period` (`all_day`, `daytime`, `evening`, `custom`), `hour_from`, `hour_to`, `staff`, `status`.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Scoped via `ClubAccessContext` and [`CanViewClubReports`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/clubs/permissions.py).
- Accessible only to Platform Admins, Owners, and Managers.
- Staff users are denied access (403 Forbidden).

## Cross-App Dependencies

- Queries `apps.bookings`, `apps.courts`, `apps.transactions`, and `apps.accounts`.

## Testing

- Test suite: [`apps/reports/tests/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/reports/tests/).
- Key test file: `test_court_usage_report.py` (clipping, multi-day ranges, demand buckets, role permissions).
