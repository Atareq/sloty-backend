# Dashboard — Agent Guide

## Responsibility

`apps/dashboard/` owns read-only operational and financial analytics, schedule calendars, court availability endpoints, and public availability views. It contains no database models or migrations.

## Domain Invariants

- **Operational vs. Financial Access Separation**:
  - Operational metrics (court availability, calendar, summary operational cards) are accessible to Staff for their assigned court.
  - Financial endpoints (`overview`, `revenue`, `court-utilization`) are restricted to Platform Admins, Owners, and Managers; Staff receive 403.
  - The `/dashboard/summary/` endpoint supports both: Staff receive operational counts, while all financial fields are set to `null` (`can_view_financial_summary() == False`).
- **Unsettled Transactions Authority**:
  - Dashboard settlement metrics are computed from live unsettled transactions (`is_cancelled=False` and `settlement_line IS NULL`), **never** from `Settlement.status=PENDING`.
  - Field naming: Counts must never be named `amount`. `staff_with_unsettled_transactions_count` represents distinct collectors with unsettled candidates.
- **Date Authority Rules**:
  - Booking activity metrics use occurrence dates (`Booking.start_time`).
  - Transaction revenue and payment method metrics use server recording dates (`Transaction.created`).
  - Current Custody metrics are **all-time state** and strictly ignore dashboard date range filters.
- **Clickable Dashboard Card Mappings**:
  - Every summary card must map to a corresponding list endpoint with matching filters (e.g., `needs_action_count` maps to `GET .../bookings/?needs_action=true`).
- **Sanitized Public Availability Endpoint**:
  - `GET /api/v1/public/clubs/{club_slug}/courts/{court_id}/availability/` is the deliberate public availability contract.
  - It exposes only public club/court info, date, and `AVAILABLE`/`UNAVAILABLE` status.
  - It must **never** leak customer information, booking IDs, internal statuses, transactions, prices, notes, or recurrence details.

## Service Layer

- [`apps/dashboard/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/dashboard/services.py):
  - `build_court_availability_payload()`: Computes schedule slots and blocking bookings.
  - `build_calendar_payload()` / `get_calendar_items()`: Aggregates day/week booking blocks for operational display. Customer `title` / `customer_name` / `customer_phone` are read from `Booking.club_player` (exact version) via `apps.bookings.identity`, falling back to snapshot columns when `club_player` is null.
  - `build_dashboard_summary_payload()`: Enforces operational vs. financial access splitting.
  - `build_dashboard_overview_payload()`, `build_dashboard_revenue_payload()`, `build_court_utilization_payload()`.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/courts/{court_id}/availability/`: Authenticated court slot availability.
- `/api/v1/public/clubs/{club_slug}/courts/{court_id}/availability/`: Sanitized public availability.
- `/api/v1/clubs/{club_slug}/calendar/`: Operational calendar endpoint.
- `/api/v1/clubs/{club_slug}/dashboard/summary/`: Combined operational/financial summary.
- `/api/v1/clubs/{club_slug}/dashboard/overview/`: High-level operations overview.
- `/api/v1/clubs/{club_slug}/dashboard/revenue/`: Financial revenue breakdowns.
- `/api/v1/clubs/{club_slug}/dashboard/court-utilization/`: Utilization percentages and hours.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Scoped via `ClubAccessContext`.
- Staff access is strictly court-scoped to their assigned court and operational-only.
- Financial metrics require `can_view_financial_summary()` (Platform Admin, Owner, Manager).

## Cross-App Dependencies

- Aggregates data across `apps.bookings`, `apps.courts`, `apps.transactions`, and `apps.settlements`. Calendar identity uses `apps.bookings.identity` (ClubPlayer).

## Testing

- Test suite: [`tests/dashboard/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/dashboard/).
- Key test file: `test_dashboard_api.py` (availability, summary role filtering, revenue, calendar).
