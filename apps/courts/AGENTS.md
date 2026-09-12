# Courts — Agent Guide

## Responsibility

`apps/courts/` owns court configuration, weekly working hours, child pricing periods, slot grid calculations, and time boundary semantics.

## Domain Invariants

- **Pricing Periods as Price Authority**:
  - `Court.default_price` is legacy migration data and must never be used for slot generation, new booking creation, or rescheduling price calculation.
  - Pricing is defined exclusively by child `CourtWorkingHourPricePeriod` rows tied to `CourtWorkingHour`.
- **Contiguous Operating Hours**:
  - Every open weekday must have contiguous, non-overlapping pricing periods aligned with `Court.slot_duration_minutes`.
  - A weekday with no pricing periods represents a closed day.
- **Midnight & Time Semantics** (`apps/courts/pricing.py`):
  - `time(0, 0)` as an end time represents `24:00` / next-day midnight (`MIDNIGHT_END_MINUTES = 1440`).
  - `is_valid_period_bounds(starts_at, ends_at)`: Validates that start time is strictly before end time using the 24:00 semantic for midnight end.
  - **Single Operational Day**: Intervals ending at `00:00` on `date + 1` belong to the operational date of start. Crossing *past* next-day midnight is disallowed (no general overnight scheduling).
  - Boundaries and durations must strictly align with `slot_duration_minutes`.
- **Weekly Working Hours Replacement**:
  - Working hours and pricing are managed atomically across the week. Direct single-period or single-weekday mutations via the legacy endpoint are rejected with `WORKING_HOURS_USE_WEEKLY_ENDPOINT` (409 Conflict).

## Important Models & Fields

- [`Court`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/models.py):
  - `slot_duration_minutes`: Grid interval for slots (default 60).
  - `requires_digital_payment_reference`: Policy flag requiring reference for digital payments.
  - `minimum_deposit`: Minimum required first payment and late-cancellation retained amount.
  - `internal_hold_expiry_hours`: Duration before unconfirmed `HOLD` bookings become eligible for expiry (default 12).
  - `cancellation_refund_notice_days`: Notice window required for full cancellation refund (null = unconfigured; 0 = until start).
- [`CourtWorkingHour`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/models.py):
  - One record per court per weekday (`0` = Monday to `6` = Sunday).
- [`CourtWorkingHourPricePeriod`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/models.py):
  - Child periods defining `starts_at`, `ends_at`, and `price`. Ordered by `starts_at`.

## Service Layer

- [`apps/courts/pricing.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/pricing.py):
  - `calculate_booking_price()`: Calculates agreed booking price from working-hour pricing periods.
  - `time_to_minutes()` / `datetime_for_local_date()`: Enforces timezone-aware Cairo time and midnight end conversions.
- [`apps/courts/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/courts/services.py):
  - `replace_weekly_working_hours()`: Replaces all working hours and pricing periods for a court atomically.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/courts/`:
  - Create/Update restricted to Platform Admins and Club Owners. Managers and staff receive 403.
- `/api/v1/clubs/{club_slug}/courts/{court_id}/working-hours/`:
  - Weekly schedule endpoint (`CourtWeeklyWorkingHoursViewSet`).
  - Gated by `can_manage_working_hours`: Platform Admins, Owners, and Managers with `manager_can_change_pricing=True`.
- `/api/v1/clubs/{club_slug}/court-working-hours/`:
  - Deprecated legacy endpoint: Read-compatible, but writes reject with `WORKING_HOURS_USE_WEEKLY_ENDPOINT`.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Staff can list and retrieve only their assigned court (`ClubMembership.court`).
- Managers can view all club courts, but cannot mutate court fields.
- `manager_can_change_pricing` on `ClubMembership` permits managers to update working hours and pricing periods, but never court model fields.

## Cross-App Dependencies

- Consumed by `apps/bookings/` for price calculation, slot generation, and hold expiry.
- Consumed by `apps/dashboard/` and `apps/reports/` for availability and utilization metrics.

## Testing

- Test suite: [`tests/courts/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/courts/).
- Key test file: `test_court_api.py` (court configuration, pricing periods, and weekly replacement).
