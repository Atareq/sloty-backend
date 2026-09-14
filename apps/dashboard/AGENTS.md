# Dashboard — Agent Guide

## Responsibility

`apps/dashboard/` owns read-only operational and financial analytics, schedule calendars, court availability endpoints, and public availability views. It contains no database models or migrations.

## Domain Invariants

- **Operational vs. Financial Access Separation**:
  - Operational metrics (court availability, calendar, summary operational cards) are accessible to Staff for their assigned court.
  - Financial endpoints (`overview`, `revenue`, `court-utilization`) are restricted to Platform Admins, Owners, and Managers; Staff receive 403 from `ROLE_PERMISSIONS["DashboardViewSet"]`.
  - The `/dashboard/summary/` endpoint supports both: Staff receive operational counts, while all financial fields are set to `null` (`can_view_financial_summary() == False`). This is a read-model presentation rule, not a second security engine.
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
  - `build_calendar_payload()` / `get_calendar_items()`: Aggregates day/week booking blocks for operational display. Customer `title` / `customer_name` / `customer_phone` are read from `Booking.club_player` (exact version) via `apps.bookings.identity`.
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

## Authorization & Scoping (Authorization Spine)

Dashboard is a **read model**. There is no Dashboard table, so authenticated endpoints compose `ClubScopedViewMixin` + `SlotyBasePermission` (`permission_view_name="DashboardViewSet"`). They do **not** use `SlotyScopedResourceMixin`. Public availability stays `AllowAny`.

Authorized source querysets are built **before** aggregation via source-domain `authorization_config`:

| Endpoint | WHO (matrix) | Source boundary |
| :--- | :--- | :--- |
| `availability` | All club roles | Court in URL; Staff unassigned court → 403 |
| `calendar` | All club roles | Booking Club + Court |
| `summary` | All club roles | Bookings/courts Club + Court; financial fields null for Staff; custody Club + optional Collector (explicit `court`/`collected_by` query filters only) |
| `overview` / `revenue` / `court_utilization` | Admin / Owner / Manager | Bookings & period transactions Club + Court (all club courts for these roles); custody Club + optional Collector; settled settlement totals Club (never collector narrowing) |

Do not apply Transaction Staff `created_by` narrowing to dashboard financial aggregations. Do not apply Settlement collector narrowing to dashboard settlement totals. Do not implicitly court-scope custody from Staff assignment.

Dashboard views use Spine source querysets. They do not use `ClubScopedAccessMixin` (no implicit `last_sync_at` updates).

## Cross-App Dependencies

- Aggregates data across `apps.bookings`, `apps.courts`, `apps.transactions`, and `apps.settlements`. Calendar identity uses `apps.bookings.identity` (ClubPlayer).

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/dashboard/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/dashboard/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/dashboard/).
- Key test files:
  - `test_dashboard_api.py`: availability, summary role filtering, revenue, calendar.
  - `test_dashboard_authorization.py`: Spine composition, club isolation of totals, Staff court vs Owner custody.
