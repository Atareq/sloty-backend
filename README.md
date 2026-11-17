# Sloty Backend

REST API for running sports court rentals: clubs, courts, bookings,
payments, refunds, cash settlements and an audit trail. It serves the
[Sloty web app](https://github.com/Atareq/sloty-frontend), an Arabic-first
PWA for football courts in Egypt.

Python 3.12 · Django 4.2 · Django REST Framework · PostgreSQL 17 · JWT ·
Cloudflare R2 · GitHub Actions · systemd

## The problem

A club rents courts by the hour. Desk staff take bookings, collect cash or
digital payments, and later hand the money over to the owner. The backend
has to guarantee that:

- a court slot is never sold twice, even when two staff book at once;
- a weekly regular keeps their slot without the system generating rows for
  every future week;
- an unpaid hold expires and frees the slot;
- a request retried after a dropped connection is applied once;
- every payment can be traced from the collector to an owner settlement.

## What it does

- **Setup:** clubs, courts, weekly working hours with priced periods, and
  court images converted to WebP and served from Cloudflare R2.
- **Bookings:** `HOLD` → `CONFIRMED` on the first payment → `COMPLETED`,
  `CANCELLED` (with a calculated refund), `NO_SHOW` or `EXPIRED`. Rescheduling
  re-checks availability and raises the price if the new slot costs more.
- **Recurring bookings:** an active weekly booking reserves future weeks
  virtually; completing it can roll it to the next week with a deposit.
  Rules are in the [recurrence contract](docs/recurring-bookings-contract.txt).
- **Money:** payments and refunds are signed transactions. The staff member
  who collected the money holds it as custody until a settlement records the
  handover.
  Rules are in the [financial consistency contract](docs/financial-consistency-contract.txt).
- **Visibility:** an audit log of booking and money events, a dashboard,
  reports, and public court discovery with sanitized availability.

## Architecture

A modular monolith with one Django app per domain under [`apps/`](apps/).

```text
Client ── JWT ──> DRF views ──> serializers ──> services ──> PostgreSQL
                  (thin)        (input rules)    (business rules,
                                                  transactions, row locks,
                                                  audit log)

systemd timers on the host:
  expire_hold_bookings   every 5 minutes
  backup-sloty-db        daily pg_dump
```

- **Business rules live in `services.py`.** Views parse and authorize;
  services own `transaction.atomic()` blocks, locking and audit records.
- **Authorization is resolved per request** in
  [`apps/common/authorization/`](apps/common/authorization/). A user's
  Profile (admin, owner, or staff with assigned courts) decides the club and
  court scope, and `scoped_queryset` narrows business querysets to it. JWT
  claims are convenience data and are never trusted for access.
- **Errors carry stable codes** (`BOOKING_SLOT_UNAVAILABLE`,
  `RECURRING_UNAVAILABLE`, `SESSION_EXPIRED`) so clients branch on `code`,
  not on the localized message.

## Reliability

| Risk | How it is handled | Evidence |
| --- | --- | --- |
| Two bookings for one slot | Booking creation locks the court row and re-checks overlaps inside the transaction. | [`create_booking`](apps/bookings/services.py), `test_overlapping_booking_create_allows_only_one_winner` |
| Retried requests | Bookings and payments accept a `client_request_id`, unique per club in the database. A replay returns the original result; the same key with a different payload returns `409`. | `test_idempotent_booking_retry_race_creates_one_attempt_and_booking` |
| Overpayment | The payment locks its booking and validates the amount against the remaining balance under that lock. | `test_payment_race_cannot_overpay_remaining_amount` |
| Duplicate payment reference | A partial unique constraint per club. | `unique_non_blank_payment_reference_per_club`, `test_duplicate_payment_reference_race_rejects_second` |
| Settling money twice | Settlement re-queries and locks the candidate transactions and never trusts client-sent IDs. | `test_settlement_race_settles_transactions_once` |
| Hold expiry racing a payment | Both paths lock the booking, so only one state change applies. | `test_hold_expiry_versus_payment_does_not_double_apply` |
| Unsafe production setup | Production settings refuse SQLite, and the demo seed command refuses production settings. | [`config/settings/production.py`](config/settings/production.py) |

The concurrency tests in
[`tests/test_postgresql_concurrency.py`](tests/test_postgresql_concurrency.py)
start real threads against PostgreSQL, because SQLite ignores
`select_for_update`.

## Testing

About 1,300 pytest tests cover the API, permissions per role, business
rules and the deploy workflow. They run on SQLite by default:

```bash
pytest -n auto
```

The PostgreSQL concurrency suite needs a PostgreSQL database:

```bash
DB_ENGINE=postgresql DB_NAME=... DB_USER=... DB_PASSWORD=... \
DB_HOST=127.0.0.1 DB_PORT=5432 pytest tests/test_postgresql_concurrency.py
```

CI runs pre-commit (black, isort, flake8), `manage.py check`, a missing
migrations check, the full suite, and the concurrency suite on PostgreSQL 17.

## Running locally

```bash
cp .env.example .env
python -m pip install -r requirements/dev.txt
python manage.py migrate
python manage.py seed_demo_data   # optional local demo data
python manage.py runserver
```

Swagger UI is at `http://127.0.0.1:8000/api/v1/docs/`. See
[docs/development.md](docs/development.md) for PostgreSQL, query
performance logging and the demo accounts.

## Deployment

Pull requests and pushes to `dev` and `master` run the test jobs. A push to
`master` then calculates the release tag, SSHs to the server, runs
[`scripts/deploy-sloty-backend`](scripts/deploy-sloty-backend) for that exact
commit, and publishes a GitHub Release. The server runs gunicorn under
systemd with PostgreSQL 17. See [docs/operations.md](docs/operations.md).

## Tradeoffs and limitations

- Slot overlap is enforced by locking in the service layer, not by a
  PostgreSQL exclusion constraint, so every write path must go through the
  services.
- The main suite runs on SQLite for speed; lock behaviour is proven only by
  the PostgreSQL suite.
- There is no task queue. Periodic work runs from systemd timers, which keeps
  operations simple but ties the app to a single host.
- Image uploads are processed synchronously inside the request.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/api.md](docs/api.md) | Endpoint reference by resource |
| [docs/development.md](docs/development.md) | Local setup, demo data, tooling |
| [docs/operations.md](docs/operations.md) | Deployment, environment files, backups |
| [docs/financial-consistency-contract.txt](docs/financial-consistency-contract.txt) | Custody and settlement rules (locked) |
| [docs/recurring-bookings-contract.txt](docs/recurring-bookings-contract.txt) | Recurrence rules (locked) |
| [docs/scheduling-contract.md](docs/scheduling-contract.md) | Working hours and slot rules |
| [docs/image-pipeline.md](docs/image-pipeline.md) | Court image processing and storage |
| [docs/architecture/](docs/architecture/) | Security architecture and booking migration audit |
| [docs/testing/](docs/testing/) | V1 QA reports (September 2026) |
| [AGENTS.md](AGENTS.md) | Engineering conventions |

`docs/business-analysis.txt`, `docs/documentation.txt` and `docs/sprints.txt`
are historical planning notes, not the current design.
