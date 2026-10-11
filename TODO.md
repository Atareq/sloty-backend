# TODO — PR Plan

Engineering backlog for `sloty-backend`, delivered as **six independent PRs**,
each branching off `dev`. They can be reviewed and merged in any order; the
notes below call out the two places where branches touch the same lines.

**Status: all six are implemented.** This file is kept as the index and as the
record of the decisions behind them.

This file follows the repository rules in [`AGENTS.md`](AGENTS.md): code and tests
are the source of truth, `SIMPLICITY WINS`, and scoped domain guides must be read
before touching a domain app.

---

## The PRs

| # | Branch | Title | Notes |
| :--- | :--- | :--- | :--- |
| 1 | `chore/docker-dev-prod` | Docker dev + production stacks | Adds the `REDIS_URL`-driven `CACHES` block |
| 2 | `refactor/unified-error-response` | One `ErrorResponse` class for all errors | No wire changes; existing tests pass unedited |
| 3 | `feat/open-api-rate-limit` | Rate limiting on unauthenticated endpoints | Ships the **same** `CACHES` block as PR 1 so it stands alone |
| 4 | `feat/captcha-toggle` | CAPTCHA, enabled from settings | Off by default; login only |
| 5 | `feat/base-model-soft-delete` | Shared base models + soft delete + managers | Touches every app; full suite green |
| 6 | `chore/security-performance-review` | Review document + production hardening | Ten findings, fixed or deferred with reasons |

### Overlaps to expect when merging

- **PRs 1 and 3** both add the `REDIS_URL` / `CACHES` block to
  `config/settings/base.py`. The text is byte-identical, so whichever merges
  second resolves trivially. PR 3 needs it because its `common.E001` deploy check
  is meaningless without a configurable cache.
- **PRs 3, 4 and 6** each append a section to `.env.example`. Conflicts there are
  additive and resolve by keeping both.

### Conventions for every PR

- Run `pytest -n 4 --reuse-db` before opening. Retry a failed node sequentially
  before calling it a regression (root `AGENTS.md` §8).
- Note: throttling is disabled in `config/settings/test.py` from PR 3 onward.
  Business suites sign in dozens of times per module and must not depend on a
  wall-clock rate window.
- Every model change ships a migration. No data migrations inside schema migrations.
- Update the scoped `apps/<domain>/AGENTS.md` when behavior in that domain changes.
- Keep the diff to the PR's stated scope. Findings outside scope get written down
  here or in PR 6, not fixed silently (root `AGENTS.md` §9).

---

## PR 1 — `chore/docker-dev-prod`

**Goal:** A `docker/` folder with separate dev and production stacks, both reading
every value from `.env`.

Current state: no Dockerfile, no compose file, no `CACHES` setting anywhere.
`config/settings/base.py` already loads `.env` through `python-dotenv` and already
switches `DB_ENGINE` between sqlite and postgres, so the settings layer needs no
redesign — only new variables.

### Scope

```text
docker/
├── dev/
│   ├── Dockerfile              # python:3.12-slim, requirements/dev.txt, runserver
│   └── docker-compose.yml      # web + db (postgres) + redis
├── prod/
│   ├── Dockerfile              # multi-stage, requirements/base.txt, gunicorn, non-root user
│   └── docker-compose.yml      # web + db + redis, restart policies, healthchecks
└── README.md                   # how to run each stack
```

- Both compose files use `env_file: ../../.env` and declare **no** hardcoded
  credentials, hostnames, ports, or secrets.
- Extend [`.env.example`](.env.example) with every new key (`DB_HOST`, `DB_PORT`,
  `REDIS_URL`, `WEB_PORT`, `GUNICORN_WORKERS`, …). `.env.example` is the contract;
  a missing key there is a bug.
- Add `CACHES` to `config/settings/base.py` driven by `REDIS_URL`, falling back to
  LocMemCache when unset so local sqlite runs and the test suite keep working.
- Production image runs as a non-root user, runs `collectstatic` at build time, and
  does **not** run `migrate` automatically on container start.
- Healthcheck hits the existing unauthenticated `GET /health/`
  ([`apps/common/views.py`](apps/common/views.py)), which is already in-process only
  (no DB, no JWT) and is the right probe for this.
- `.dockerignore` excluding `.git`, `.venv`, `staticfiles`, `__pycache__`, `.env`.

### Out of scope

The production host deploy path stays exactly as it is today. `scripts/deploy-sloty-backend`
is the canonical production deploy (root `AGENTS.md` §10) and this PR must not
change, replace, or "dockerize" it. The prod image is for parity and future use.

### Acceptance

- `docker compose -f docker/dev/docker-compose.yml up` gives a working API against
  PostgreSQL with no edits to any file other than `.env`.
- `grep -rE '(password|secret|5432|6379)' docker/**/docker-compose.yml` returns only
  variable references, never literals.
- `pytest -n 4 --reuse-db` still passes on sqlite outside Docker.

---

## PR 2 — `refactor/unified-error-response`

**Goal:** One class builds every error response body. No second error shape anywhere.

Current state is already close, which makes this a consolidation, not a rewrite:
[`apps/common/exceptions.py`](apps/common/exceptions.py) has `SlotyAPIException` and
`sloty_exception_handler`, and the whole `apps/` tree contains exactly **one** raw
`Response({...})` error dict. The work is to make the envelope a single, typed,
tested object rather than two dict literals built inline in the handler.

### Scope

- Introduce one builder — `ErrorResponse` (or `error_payload()`) — in
  `apps/common/exceptions.py` that owns the full body and is the **only** place a
  response dict is constructed:
  ```json
  {"success": false, "code": "STABLE_CODE", "message": "…", "details": {}, "field_errors": {}}
  ```
- Collapse the two branches in `sloty_exception_handler` (validation vs. business)
  into one call into that builder. Keep both current wire shapes byte-identical —
  this is a refactor, not a contract change. `field_errors` stays present only for
  `VALIDATION_ERROR`; `details` only for the rest.
- Register every stable error code in one enum/registry module
  (`apps/common/error_codes.py`) and have domains import from it instead of passing
  free-text `code=` strings. Codes are API keys; treat renaming one as a breaking change.
- Replace the remaining raw `Response({...})` error and any `assert`-style error text
  with `SlotyAPIException`.
- Add the codes that PRs 3 and 4 will need: `THROTTLED`, `CAPTCHA_REQUIRED`,
  `CAPTCHA_INVALID`, `CAPTCHA_UNAVAILABLE`.

### Acceptance

- `tests/common/` gains a test asserting the exact envelope for: a DRF
  `ValidationError`, a `SlotyAPIException`, a 404, a 403, a 500, and DRF's own
  `Throttled`.
- A grep for `"success": False` returns exactly one hit in the whole repository.
- No response body changes. Existing `tests/` pass unmodified; if a test needs
  editing, the refactor changed a contract and the change is wrong.

---

## PR 3 — `feat/open-api-rate-limit`

**Goal:** Rate limit the unauthenticated surface. Authenticated business traffic is
unaffected.

Current state: `REST_FRAMEWORK` in `config/settings/base.py` declares no
`DEFAULT_THROTTLE_CLASSES` and no `DEFAULT_THROTTLE_RATES`. There is no rate limiting
anywhere in the project.

### The open surface to cover

| Endpoint | Source | Proposed scope | Notes |
| :--- | :--- | :--- | :--- |
| `POST /api/v1/auth/token/` | `apps/accounts` | `auth_login` | Strictest. Credential-stuffing target. Throttle by IP **and** by submitted username. |
| `POST /api/v1/auth/token/refresh/` | `apps/accounts` | `auth_refresh` | Looser than login; legitimate clients refresh often. |
| `GET /api/v1/public/clubs/{slug}/courts/{id}/availability/` | [`apps/dashboard/urls.py:15`](apps/dashboard/urls.py) | `public_availability` | The only public business endpoint. Shareable link — expect real anonymous volume, so do not set this too tight. |
| `GET /api/v1/egypt-locations/` | [`apps/common/views.py:16`](apps/common/views.py) | `public_static` | Static payload; consider caching it instead of/alongside throttling. |
| `GET /api/v1/schema/`, `/api/v1/docs/` | [`config/urls.py:28`](config/urls.py) | `public_static` | Also decide whether these stay `AllowAny` in production at all (see PR 6). |
| `GET /health/` | [`apps/common/views.py:28`](apps/common/views.py) | **exempt** | Deploy probe. Throttling it breaks `scripts/deploy-sloty-backend`. |

### Scope

- `ScopedRateThrottle` plus a named-scope map; every rate comes from `.env` via the
  existing `env_int()` helper so ops can retune without a deploy.
- **Requires the Redis cache from PR 1.** DRF throttling stores counters in the
  Django cache; the default LocMemCache is per-process, so with multiple gunicorn
  workers each worker would enforce its own separate limit. A throttle on LocMemCache
  is not a rate limit — treat Redis as a hard prerequisite in production.
- Trust `X-Forwarded-For` only through an explicit `NUM_PROXIES` setting. Behind
  Nginx, the naive client-IP read yields the proxy IP and throttles all users as one.
- 429 responses must go through PR 2's builder with code `THROTTLED` and a
  `Retry-After` header.
- Never throttle by authenticated user for club-scoped business endpoints in this PR.
  Staff hammering the schedule during a busy evening is normal traffic.

### Acceptance

- `tests/common/test_throttling.py`: each scope returns 429 at its configured limit,
  `/health/` never throttles, and the 429 body matches the unified envelope.
- Tests run against a `LocMemCache` marked clearly as test-only, and clear the cache
  between tests to avoid cross-test bleed under `-n 4`.

---

## PR 4 — `feat/captcha-toggle`

**Goal:** CAPTCHA verification that is **off by default** and enabled entirely from
settings/`.env`.

### Scope

- Settings: `CAPTCHA_ENABLED` (default `False`), `CAPTCHA_PROVIDER`,
  `CAPTCHA_SECRET_KEY`, `CAPTCHA_VERIFY_URL`, `CAPTCHA_TIMEOUT_SECONDS`,
  `CAPTCHA_MIN_SCORE`. All via `env_bool`/`env_int` in `config/settings/base.py`.
- One verification service, `apps/common/captcha.py`, with a single entry point
  `verify_captcha(token, *, remote_ip) -> None` that raises `SlotyAPIException`.
  Provider-specific detail stays inside this module; no domain app imports an HTTP
  client or knows the provider name.
- When `CAPTCHA_ENABLED = False` the code path is a no-op: no token read, no network
  call, no serializer field required. The disabled path must be the cheap one.
- Applied to: login, and any future signup / password-reset flow. **Not** applied to
  public availability — it is a shareable read link for players and a CAPTCHA there
  destroys the product (see `apps/dashboard/AGENTS.md`, public availability contract).
- Serializers accept the token as a write-only field; services never see it.

### Open decisions — need a call before implementing

1. **Provider.** Recommend **Cloudflare Turnstile**: free, no Google dependency,
   lighter privacy footprint, works well in Egypt. Alternatives: reCAPTCHA v3, hCaptcha.
2. **Behavior when the provider is unreachable.** Fail-closed is the secure answer but
   an outage at the provider then locks every staff member out of the app on a busy
   evening. Recommend: **fail-closed with a 3–5s timeout, plus a documented
   `CAPTCHA_ENABLED=false` kill switch** that ops can flip without a deploy.

### Acceptance

- Tests cover: disabled (no token needed, zero network calls), enabled + valid,
  enabled + invalid (`CAPTCHA_INVALID`), enabled + missing (`CAPTCHA_REQUIRED`),
  enabled + provider timeout (`CAPTCHA_UNAVAILABLE`).
- No test performs a real network call; the verifier is mocked at the service boundary.

---

## PR 5 — `feat/base-model-soft-delete`

**Goal:** One `BaseModel` carrying `created_at`, `modified_at`, `deleted_at`,
`deleted_by`, plus a manager that exposes deleted rows.

> [!IMPORTANT]
> **Soft delete cannot be applied to every model.** Several domain invariants in this
> repository explicitly forbid it, and they are enforced by tests:
> - `apps/transactions/AGENTS.md` — immutable ledger, "no `PUT`, `PATCH`, `DELETE`".
>   Corrections are a logged cancel (`is_cancelled`), never a delete.
> - `apps/audit/AGENTS.md` — append-only; "no API create, update, or delete endpoints exist".
> - `apps/players/AGENTS.md` — "There is no `deleted_at` / soft delete";
>   "ClubPlayer rows are never deleted".
> - `apps/bookings/AGENTS.md` — lifecycle is `CANCELLED` / `EXPIRED` statuses, not deletion.
>
> **Assumption this plan is built on:** `BaseModel` gives **timestamps to every model**,
> and soft delete is a **separate opt-in mixin** applied only where deletion is a real
> operation today (`Club`, `Court`, `CourtWorkingHour`, `CourtWorkingHourPricePeriod`,
> profile children, `StaffCourtAccess`). Say the word if you want it universal instead —
> that is a product decision, but it would contradict four scoped guides and break their
> test suites, so it needs to be made deliberately.

### Current state

No shared base model exists; every model inherits plain `models.Model`, and timestamp
naming is inconsistent:

| Naming | Models |
| :--- | :--- |
| `created` + `modified` | `Booking`, `BookingAttempt`, `Transaction`, `TransactionAttempt`, `Court`, `Club`, `Profile` |
| `created` only | `AuditLog`, `StaffCourtAccess`, `SettlementTransaction` |
| `created_at` + `updated_at` | `PlayerProfile` |
| `created_at` only | `Settlement` |

`django-safedelete==1.4.1` is in `requirements/base.txt` and `"safedelete"` is in
`INSTALLED_APPS` ([`config/settings/base.py:73`](config/settings/base.py)) but **no
model uses it**.

### Scope

- `apps/common/models.py`:
  - `TimeStampedModel(models.Model)` — abstract; `created_at`, `modified_at`.
  - `SoftDeleteModel(TimeStampedModel)` — abstract; adds `deleted_at` (nullable,
    indexed) and `deleted_by` (FK to `AUTH_USER_MODEL`, `SET_NULL`, nullable),
    a `delete()` override that soft-deletes, and a `hard_delete()` escape hatch.
  - Managers: `objects` (alive only), `all_objects` (everything),
    `deleted_objects` (deleted only).
- Migrate models per the assumption above; one migration per app.

### Two traps to handle explicitly

1. **`_default_manager` and the Authorization Spine.** `apps/common/AGENTS.md`
   specifies that `scoped_queryset()` "always starts from `model._default_manager`".
   Making the alive-only manager the default therefore silently changes every
   authorized queryset in the project. That is almost certainly the behavior you want,
   but it must be an explicit, tested decision — add a case to `tests/authorization/`
   proving soft-deleted rows disappear from scoped querysets and return 404, not 403.
2. **Renaming `created` → `created_at` is not cosmetic.** `Transaction.created` is
   documented in `apps/settlements/AGENTS.md` as the **authoritative** financial
   ordering key (`order_by("created", "id")`), the source of settlement `period_start`,
   and the transaction date-filter field, explicitly distinct from `occurred_at`.
   A rename touches settlements services, filters, dashboard aggregation, and
   serializer output.

### Open decisions — need a call before implementing

1. **Rename or add?** Recommend **rename the model fields** to `created_at` /
   `modified_at` for consistency (the pre-release migration baseline already assumes
   recreated databases, so there is no production data to migrate), **but keep the
   API response keys unchanged** using `source=` on serializers. The frontend is a
   separate repo on a separate deploy cadence; changing response keys here breaks it.
2. **`django-safedelete`: use it or drop it?** Recommend **hand-rolling** the ~40 lines
   above (`SIMPLICITY WINS`, and it keeps the `_default_manager` interaction with the
   Spine fully under our control), then removing the unused dependency and its
   `INSTALLED_APPS` entry in the same PR. Alternative: adopt safedelete and write no
   custom code, accepting its policy semantics.

### Acceptance

- `tests/common/test_base_model.py`: soft delete sets both `deleted_at` and
  `deleted_by`; `objects` hides the row; `all_objects` and `deleted_objects` find it;
  `hard_delete()` removes it; restoring clears both fields.
- `tests/authorization/`: soft-deleted rows are absent from every scoped queryset.
- Full suite green: `pytest -n 4 --reuse-db`.

---

## PR 6 — `chore/security-performance-review`

**Goal:** A written review of the whole project, plus the fixes that are safe to make
in the same PR. Per root `AGENTS.md` §9, anything out of scope is documented as a
finding rather than silently fixed.

Deliverable: `docs/reviews/security-performance-<YYYY-MM>.md`, findings ranked by
severity, each with file/line, impact, and a recommendation.

### Security — start from these known gaps

- [`config/settings/production.py`](config/settings/production.py) is **four lines**
  and sets only static file paths. No `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`,
  `SECURE_PROXY_SSL_HEADER`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  `SECURE_CONTENT_TYPE_NOSNIFF`, or `X_FRAME_OPTIONS`. Run `manage.py check --deploy`
  against production settings and work the list.
- `CORS_ALLOWED_ORIGINS` is defined only in
  [`config/settings/local.py`](config/settings/local.py) — confirm what production
  actually serves and that it is not permissive by omission.
- `SECRET_KEY = os.environ.get("SECRET_KEY")` has no fail-fast when unset.
- Decide whether `/api/v1/schema/` and `/api/v1/docs/` stay `AllowAny` in production
  ([`config/urls.py:28`](config/urls.py)) — that publishes the full API surface.
- Verify the tenant-isolation rule holds everywhere: no view trusts a client-supplied
  club id, and every `get_queryset()` is Spine-scoped **before** filters run.
- Confirm the login enumeration guarantee still holds after PRs 3 and 4 — a 429 or a
  CAPTCHA challenge that fires only for existing usernames is an enumeration oracle.
- Check that `SQLQueryStatsMiddleware` never logs parameters, bodies, or auth headers
  under the new Docker log configuration.

### Performance — start from these

- N+1 sweep across list endpoints using the existing `SQL_QUERY_STATS_*` middleware
  (`[PERF:N+1?]` heuristics) with `SQL_QUERY_STATS_ENABLED=true`.
- Verify every `authorization_config` declares a `select_related` / `prefetch_related`
  load plan that actually matches what its serializer reads.
- Index review: the composite lookups behind availability, slot generation, and the
  unsettled-transactions candidate query (`is_cancelled=False AND settlement_line IS NULL`).
- Confirm `select_for_update()` lock scope is as narrow as possible in
  `create_booking()`, `cancel_transaction()`, and `settle`/`create` — wide locks on a
  busy evening serialize the whole club.
- Cache the static `GET /api/v1/egypt-locations/` payload now that Redis exists.
- Audit-log and booking list pagination behavior on large clubs.

### Acceptance

- The findings document exists and every finding is either fixed in this PR or has an
  explicit "deferred, because …" line.
- No behavior change lands without a test.

---

## Implementation notes — where the plan changed

The plan above was written before the code. Three things turned out differently,
and the reasons matter more than the plan did:

1. **Soft delete is narrower than proposed.** The plan said `Club`, `Court`,
   `CourtWorkingHour`, `CourtWorkingHourPricePeriod`, profile children and
   `StaffCourtAccess`. Implementation narrowed it to **`Club` and `Court`**.
   The working-hours and court-assignment rows are rebuilt by replace-and-recreate
   flows: a `delete()` that only marks rows leaves them behind to collide with
   `UNIQUE(court, weekday)`. This was not hypothetical — the first pass included
   them and the weekly working-hours replacement failed outright. `Profile`
   already expresses deactivation through `is_enabled`.

2. **"One raw `Response({...})` error dict" was wrong.** The single hit was the
   health check's *success* payload. There were zero ad-hoc error dicts, so PR 2
   is purely an extraction and changes no bytes on the wire.

3. **The rename surface was wider than a field rename.** Beyond model fields, the
   sweep had to leave alone, or put back: `?ordering=` query-parameter values,
   serializer `Meta.fields` wire keys, service payload dict keys, stored audit
   snapshot JSON keys, and local variables named `created`. It had to additionally
   catch ORM lookups (`created__gte`) and relation traversals
   (`lines__transaction__created`) that a naive `\bcreated\b` pattern misses.
   The first full run had 101 failures; all were rename fallout, not design
   problems.

---

## Backlog — not in this stack

- Extract per-app constants into `apps/<domain>/constants.py` where a domain has more
  than ~5 lines of constants (carried over from [`todo.txt`](todo.txt)).
- Reconcile the scoped `AGENTS.md` files: `apps/players/`, `apps/bookings/`,
  `apps/settlements/`, `apps/common/`, and the root guide each contain two
  contradictory versions of the same section. Verified against code, the **simplified**
  half is live in every case: `ClubPlayer` has no `is_current_version` /
  `previous_version` / `created_at`, there is no `create_club_player_version()`,
  `Settlement` has no `status` / `collected_by` / `created_by`, settlement services
  expose only `preview()` / `create()`, and there is no `mark-settled` route.
