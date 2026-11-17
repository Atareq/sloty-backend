# Development

## Local Setup

Create a local environment file from the safe example values:

```bash
cp .env.example .env
```

Update `.env` for your machine. The default example uses SQLite; set
`DB_ENGINE=postgresql` and the `DB_*` values when using PostgreSQL.
PostgreSQL concurrency tests skip on SQLite; run
`DB_ENGINE=postgresql pytest tests/test_postgresql_concurrency.py` when a
PostgreSQL database is available.

Install development dependencies:

```bash
python -m pip install -r requirements/dev.txt
```

Apply database migrations:

```bash
python manage.py migrate
```

Run the development server:

```bash
python manage.py runserver
```

Optional SQL request performance logs can be enabled without `DEBUG`:

```env
SQL_QUERY_STATS_ENABLED=true
SQL_QUERY_STATS_VERBOSE=false
SQL_QUERY_STATS_WARN_QUERY_COUNT=1
SQL_QUERY_STATS_SLOW_QUERY_MS=100
SQL_QUERY_STATS_SLOW_REQUEST_MS=500
SQL_QUERY_STATS_MAX_QUERY_SAMPLES=5
SQL_QUERY_STATS_MAX_SQL_LENGTH=500
```

When enabled, every API request logs a `[PERF]` summary with method, path,
status, query count, combined SQL time (`db=`), and total request time.
A `[PERF:WARN]` line is added when query count exceeds
`SQL_QUERY_STATS_WARN_QUERY_COUNT` (default `1`). That warning is an
observability threshold, not a required query budget. Duplicate SQL+params
and repeated SQL shapes (possible N+1, logged as `[PERF:N+1?]`) are
reported separately. Verbose mode may include truncated SQL structure
and never logs parameter values.

Swagger UI is available at:

```text
http://127.0.0.1:8000/api/v1/docs/
```

For development convenience, `/api/v1/docs/` and `/api/v1/schema/` are public.
Business endpoints still require authentication. Use Swagger UI to inspect APIs
and test authenticated requests by providing a bearer token.

## Demo Seed Data

Create richer local/demo data for Swagger or Postman testing:

```bash
python manage.py seed_demo_data
```

The command is idempotent and local/dev only. It creates one club with slug
`testing`; all demo users use password `123`.

Main users:

- `platform_admin`
- `owner`
- `staff`

Main club:

- `testing` (`Testing`): one local testing club with two courts, bookings,
  transactions, settlements, and audit examples.

Token examples:

```json
{"phone_number": "+201033333331", "password": "123"}
```

```json
{
  "phone_number": "+201033333331",
  "password": "123",
  "club_slug": "testing"
}
```

The seed command includes role-specific memberships, two courts, working
hours, bookings across all statuses, related transactions, pending and settled
settlements, unsettled transactions for preview testing, and audit log examples.

Run tests:

```bash
pytest
```

## Development Tools

Install pre-commit hooks:

```bash
pre-commit install
```

Run all pre-commit hooks manually:

```bash
pre-commit run --all-files
```

## Development Schema Baseline

The Profile inheritance refactor resets the pre-release migration history.
Recreate the local development database before running `python manage.py migrate`;
existing databases from the discarded history cannot be upgraded in place.
First-party model apps start at `0001_initial.py`. Django may also emit
`0002_initial.py` for circular foreign keys. Recreate reused test databases
once with sequential `pytest --create-db` (do not combine `-n` with
`--create-db` on PostgreSQL), then use the standard `pytest -n 4 --reuse-db`.
