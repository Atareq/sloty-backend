# Bookings — Agent Guide

## Responsibility

`apps/bookings/` owns booking creation, lifecycle state transitions, agreed-price snapshots, slot availability and overlap protection, booking-native recurrence, hold expiry logic, and offline attempt history.

## Domain Invariants

- **Price Snapshot Authority**:
  - `Booking.total_price` is calculated by the backend from court pricing periods at creation time. Clients must never control booking price. Price does not change when court working-hour pricing changes later.
- **Booking Statuses & Overlap Protection**:
  - Statuses: `HOLD`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`.
  - **Blocking Statuses**: `HOLD`, `CONFIRMED`, `COMPLETED`, `NO_SHOW`. These block overlapping reservations on the same court.
  - **Non-blocking Statuses**: `CANCELLED` and `EXPIRED` release their time slot.
  - Overlap collision returns `BOOKING_SLOT_UNAVAILABLE`.
  - `FREE` and `UNAVAILABLE` are display-level availability response states only and must never be added to `Booking.Status`.
- **Service-Controlled Lifecycle**:
  - All status transitions must execute through [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py) inside `transaction.atomic()` with `select_for_update()`.
  - Allowed transitions:
    - `HOLD -> CANCELLED`, `HOLD -> EXPIRED`, `HOLD -> CONFIRMED` (via first transaction).
    - `CONFIRMED -> CANCELLED`, `CONFIRMED -> COMPLETED`, `CONFIRMED -> NO_SHOW`, `CONFIRMED -> HOLD` (via transaction cancellation).
  - Terminal statuses (`COMPLETED`, `CANCELLED`, `NO_SHOW`, `EXPIRED`) cannot transition further.
- **Completion Invariant**:
  - A booking cannot transition to `COMPLETED` while any dynamic remaining amount exists (`remaining_amount > 0`).
  - Violation raises `BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT` (409 Conflict).
  - The backend must never auto-create cash transactions upon completion.
- **Hold Expiry Calculation**:
  - Single authoritative rule:
    ```text
    effective_hold_expires_at = min(
        booking.created + court.internal_hold_expiry_hours,
        booking.start_time
    )
    ```
  - Executed via `python manage.py expire_hold_bookings`. A HOLD whose start time has arrived is expired even if policy hours have not elapsed.
- **Reschedule Rules**:
  - Allowed only from `HOLD` or `CONFIRMED`.
  - Existing transactions remain attached.
  - If recalculated price is higher, update `total_price`; if lower or equal, retain existing `total_price`.
- **Booking-Native Recurrence**:
  - Recurrence is booking-native (`source=RECURRING`, `recurrence_status` in `ACTIVE`, `RENEWED`, `ENDED`). Do not create a separate recurring app or series model.
  - Future recurring slots are virtual arithmetic matches (`slot_status=RECURRING_RESERVED`).
  - Completion with `continue_recurring=true` creates the next weekly occurrence atomically and marks the anchor `RENEWED`.
  - Cancelling, no-showing, or expiring an active recurrence sets `recurrence_status=ENDED`. Active recurring bookings cannot be rescheduled.
- **Offline / PWA Sync & Idempotency**:
  - `client_request_id` is an optional UUID idempotency key scoped to the Club. Retries with the same key and payload return HTTP 200; mismatched payloads return `BOOKING_CLIENT_REQUEST_MISMATCH` (409 Conflict).
  - Historical bookings are permitted: an appointment time being before server sync time is not a rejection reason.
  - Persistent [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py) records track intent and outcomes. Rejected attempts never affect operational bookings or availability.

## Important Models & Fields

- [`Booking`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - `customer_name`, `customer_phone`, `start_time`, `end_time`, `total_price`.
  - `source`: `MANUAL`, `ADMIN_CORRECTION`, `RECURRING`.
  - `recurrence_status`: `ACTIVE`, `RENEWED`, `ENDED` (null for non-recurring).
  - `previous_recurring_booking`: Pointer to prior recurrence occurrence.
  - Traceability: `cancellation_reason`, `no_show_reason`, `reschedule_reason`, `completed_at`, `cancelled_at`, `no_show_at`, `expired_at`, `last_status_changed_by`.
- [`BookingAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/models.py):
  - Preserves requested parameters, `outcome` (`SUCCESS`/`REJECTED`), `failure_code`, `failure_details`, and `resolution` (`UNRESOLVED`, `DISMISSED`, `RESOLVED`).

## Service Layer

- [`apps/bookings/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/bookings/services.py):
  - `create_booking()`: Enforces court working hours, slot alignment, overlap check, price calculation, and idempotency.
  - `cancel_booking()`: Validates notice period, calculates refunds, creates refund transactions, updates recurrence.
  - `complete_booking()`: Validates zero remaining balance, handles recurrence continuation.
  - `reschedule_booking()`: Overlap check excluding current booking, updates price if higher.
  - `no_show_booking()`, `expire_booking()`, `expire_due_hold_bookings()`.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/bookings/`: List, create, retrieve, partial update (customer info/notes only on non-locked bookings).
- Lifecycle action routes:
  - `POST .../bookings/{id}/cancel/`
  - `POST .../bookings/{id}/complete/`
  - `POST .../bookings/{id}/no-show/`
  - `POST .../bookings/{id}/reschedule/`
  - `POST .../bookings/{id}/expire/`
  - `POST .../bookings/{id}/end-recurrence/`
  - `GET .../bookings/{id}/recurrence-next/`
- `/api/v1/clubs/{club_slug}/bookings/slots/`: Schedule slot availability generator.
- `/api/v1/clubs/{club_slug}/booking-attempts/`: Traceability list/detail and dismissal for rejected attempts.

## Authorization & Scoping (Current-State)

> [!NOTE]
> Current-state/legacy authorization rules. Do not treat as target architecture.

- Scoped via `ClubAccessContext`.
- Staff users can list, create, and manage bookings and attempts only for their assigned court.
- Staff can dismiss only their own unresolved rejected booking attempts.

## Cross-App Dependencies

- Calls `apps.courts.pricing` for price snapshots and working-hour boundary checks.
- Interacts with `apps.transactions` for payment status, cancellation refund rows, and remaining balance checks.
- Generates audit logs via `apps.audit.services`.

## Testing

- Test suite: [`tests/bookings/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/bookings/).
- Key test files:
  - `test_booking_api.py`: Creation, validation, lifecycle endpoints, recurrence.
  - `test_booking_attempt_model.py`: Idempotency, attempt persistence, and dismissal.
  - `test_egypt_timezone_boundaries.py`: Local Cairo timezone and midnight handling.
