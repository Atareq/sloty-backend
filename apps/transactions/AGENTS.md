# Transactions — Agent Guide

## Responsibility

`apps/transactions/` owns the immutable financial transaction ledger, payment recording, transaction cancellation/correction workflows, booking payment summaries, and offline transaction attempts.

## Domain Invariants

- **Immutable Financial Ledger**:
  - Transactions are immutable history: no `PUT`, `PATCH`, `DELETE`, or reversal rows exist.
  - Types & Signs:
    - `transaction_type=PAYMENT`: Amount must be strictly positive (`amount > 0`).
    - `transaction_type=REFUND`: Amount must be strictly negative (`amount < 0`).
- **Payment Validation & Deposit Threshold**:
  - The first active PAYMENT for a normal booking must be at least `min(court.minimum_deposit, booking.total_price)`.
  - Creating the first valid payment on a `HOLD` booking confirms it to `CONFIRMED`.
  - Digital payment methods (`DIGITAL_WALLET`, `BANK_TRANSFER`) require a reference when `Court.requires_digital_payment_reference=True`.
  - `payment_reference` is unique per Club when non-blank.
- **Transaction Cancellation & Corrections**:
  - Corrections use a logged cancel of the original row via `POST .../transactions/{id}/cancel/` with a required non-empty reason, followed by normal transaction creation.
  - Settled transactions (`settlement_line` exists) and already cancelled transactions cannot be cancelled.
  - Cancelled rows remain visible (`is_cancelled=True`), but are excluded from paid amount, remaining amount, settlements, and dashboard revenue.
  - If cancelling a transaction leaves a `CONFIRMED` booking with zero remaining valid payments, the booking is reverted `CONFIRMED -> HOLD` in the same atomic transaction.
  - `REFUND` transactions cannot be cancelled via the normal cancellation endpoint.
- **Financial Event Timestamp vs Persistence**:
  - `occurred_at`: When the transaction financially took place (supports historical sync).
  - `created`: Server persistence timestamp; remains authoritative for transaction date filters, Current Custody `period_start`, and settlement ordering.
- **Offline / PWA Sync & Attempts**:
  - Optional `client_request_id` UUID provides idempotency scoped to the Club.
  - Persistent [`TransactionAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py) records log payment intent and backend outcomes. Rejected or dismissed attempts never affect financial totals or custody.

## Important Models & Fields

- [`Transaction`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py):
  - `club`, `court`, `booking`, `amount`, `transaction_type`, `payment_method`, `payment_reference`.
  - `is_cancelled`, `cancelled_by`, `cancelled_at`, `cancellation_reason`.
  - `occurred_at`, `created`.
- [`TransactionAttempt`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/models.py):
  - Tracks requested transaction intent, `outcome` (`SUCCESS`/`REJECTED`), `failure_code`, `failure_details`, and `resolution`.

## Service Layer

- [`apps/transactions/services.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/transactions/services.py):
  - `create_transaction()`: Enforces court deposit rules, digital references, updates booking `HOLD -> CONFIRMED`, and audits.
  - `cancel_transaction()`: Enforces unsettled status, marks cancelled, recalculates booking status (`CONFIRMED -> HOLD` if unbacked), and audits.
  - `annotate_booking_paid_amount()`: Authoritative aggregation of non-cancelled payment rows minus refunds.

## API Boundaries & Key Invariants

- `/api/v1/clubs/{club_slug}/transactions/`: List and create transactions.
- `/api/v1/clubs/{club_slug}/transactions/{id}/`: Retrieve transaction detail.
- `POST .../transactions/{id}/cancel/`: Transaction correction endpoint requiring `cancellation_reason`.
- `/api/v1/clubs/{club_slug}/transaction-attempts/`: Traceability list/detail and dismissal for rejected attempts.

## Authorization & Scoping (Authorization Spine v2)

> Club/court **WHERE** belongs to the Authorization Spine. `apps/transactions/authorization.py` keeps **business conditions** the matrix cannot express: Staff `created_by` / `attempted_by` narrowing, cancel-own (except Platform Admin), dismiss-own, and create-on-authorized-booking.

- **ViewSets:** `SlotyScopedResourceMixin` + `SlotyBasePermission`. Models declare `authorization_config` (`default_scope="court"`). The secured queryset is built before filters, pagination, and serializers.
- **Operational boundary:** Club + Court. Settlement / Current Custody is separately Spine v2 club-scoped with collector narrowing — Club + optional Collector, **never** Court.
- **Staff list/retrieve:** assigned court(s) **and** `created_by=request.user` (collector self-scope). This is not applied to Bookings.
- **Staff create:** any booking on an assigned court (not creator-scoped).
- **Cancel:** Platform Admin may cancel any in-scope transaction. Owner, Manager, and Staff may cancel only their own collections. Out-of-scope rows are HTTP 404; in-scope rows the actor may not cancel are HTTP 403.
- **TransactionAttempt:** Club + Court, then Staff `attempted_by=request.user`. Dismiss is attempter-only for every role.
- Transaction ViewSets use the Spine only. They do not call `ClubAccessContext`.

## Cross-App Dependencies

- Depends on `apps.bookings` (linked booking lifecycle and payment status updates). List/detail `booking_customer_name` / `booking_customer_phone` are sourced from `Booking.club_player` (exact version at booking time) — ClubPlayer is the historical identity, not a live rewrite.
- Consumed by `apps.settlements` (unsettled transaction candidate set) and `apps.dashboard` (revenue analytics).

## Testing

- Run with the project-standard command: `pytest -n 4 --reuse-db tests/transactions/`. Retry a failed node sequentially only if the parallel run reports a failure (see root `AGENTS.md` §8).
- Test suite: [`tests/transactions/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/transactions/).
- Key test files:
  - `test_transaction_api.py`: Creation, validation, deposit rules, idempotency.
  - `test_transaction_authorization.py`: Spine v2 contract, club/court isolation, Staff collector scope, cancel/dismiss, custody separation.
  - `test_transaction_cancel_api.py`: Cancellation, booking status reversion, settlement guards.
  - `test_transaction_attempt_model.py`: Attempt persistence and dismissal.
