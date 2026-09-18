# Security & Performance Review — September 2026

Scope: whole of `sloty-backend` at branch point `dev`.
Method: `manage.py check --deploy`, settings audit, authorization-boundary read,
query-plan read of the hot endpoints, and the `SQL_QUERY_STATS_*` middleware.

Per root `AGENTS.md` §9, findings outside this PR's scope are documented rather
than silently fixed. Each one below says whether it was fixed here or deferred.

---

## Summary

| # | Severity | Finding | Status |
| :--- | :--- | :--- | :--- |
| S1 | **High** | Production settings carried no security headers at all | **Fixed** |
| S2 | **High** | No rate limiting anywhere on the open API | Deferred → `feat/open-api-rate-limit` |
| S3 | Medium | Production CORS allowed no origin, and was configured nowhere | **Fixed** |
| S4 | Medium | `/api/v1/schema/` and `/api/v1/docs/` are `AllowAny` in production | Deferred — needs a product decision |
| S5 | Medium | `SECRET_KEY` has no fail-fast and no length floor | Deferred |
| S6 | Low | Login has no bot protection | Deferred → `feat/captcha-toggle` |
| P1 | **High** | No shared cache; anything needing shared counters is per-worker | Deferred → `chore/docker-dev-prod` |
| P2 | Medium | Custody candidate query has no covering index for its exact filter | Deferred |
| P3 | Medium | `/api/v1/egypt-locations/` recomputes a static payload per request | Deferred |
| P4 | Low | `SECURE_SSL_REDIRECT` would have broken the deploy health gate | **Fixed pre-emptively** |

---

## Security

### S1 — Production settings had no security headers · **High** · Fixed

`config/settings/production.py` was four lines and set only `STATIC_URL` and
`STATIC_ROOT`. `manage.py check --deploy` reported:

```text
security.W004  SECURE_HSTS_SECONDS not set
security.W008  SECURE_SSL_REDIRECT not True
security.W012  SESSION_COOKIE_SECURE not True
security.W016  CSRF_COOKIE_SECURE not True
security.W018  DEBUG True in deployment
```

**Impact.** Session and CSRF cookies could travel over plain HTTP; no HSTS, so a
first-visit downgrade was possible; a stray `DEBUG=True` in `.env` would publish
tracebacks containing settings and SQL to anyone who could trigger a 500.

**Fixed.** `production.py` now sets `SECURE_PROXY_SSL_HEADER`,
`SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS` (1 year) with subdomains,
`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`,
`SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY`, `X_FRAME_OPTIONS`, and
forces `DEBUG = False` regardless of `.env`. Remaining `check --deploy` output is
`security.W021` (HSTS preload), left off deliberately — preload submission is
hard to reverse.

### S2 — No rate limiting on the open API · **High** · Deferred

`REST_FRAMEWORK` declared no `DEFAULT_THROTTLE_CLASSES` and no
`DEFAULT_THROTTLE_RATES`. `POST /api/v1/auth/token/` accepted unlimited attempts
from one source, and the public availability endpoint was unbounded.

Addressed in full on `feat/open-api-rate-limit`. Not duplicated here to keep the
branches independent.

### S3 — Production CORS was configured nowhere · Medium · Fixed

`CORS_ALLOWED_ORIGINS` appeared only in `config/settings/local.py`, listing the
Vite dev server. `base.py` set none, so production inherited an empty list.

**Impact.** This is a correctness problem before it is a security one: the real
frontend origin was never allowed. The risk is the likely *reaction* to it —
reaching for `CORS_ALLOW_ALL_ORIGINS = True` under deadline pressure.

**Fixed.** `production.py` reads `CORS_ALLOWED_ORIGINS` (comma-separated) and
`CORS_ALLOW_CREDENTIALS` from `.env`. An empty value keeps the safe default of
allowing nothing; there is no "allow all" switch. Pinned by
`test_cors_defaults_to_allowing_nothing`.

### S4 — Schema and docs are public in production · Medium · Deferred

[`config/urls.py:28`](../../config/urls.py) and `:35` mount `SpectacularAPIView`
and `SpectacularSwaggerView` with `permission_classes=[AllowAny]`. Anyone can
read the complete API surface — every endpoint, parameter, and error code.

Not a vulnerability by itself, and public docs are a legitimate choice. But it is
a choice, and right now it is an accidental one. **Needs a product decision**: keep
public, restrict to authenticated users, or serve only outside production.

### S5 — `SECRET_KEY` has no fail-fast · Medium · Deferred

[`config/settings/base.py`](../../config/settings/base.py) does
`SECRET_KEY = os.environ.get("SECRET_KEY")`. When unset, the process starts and
fails later inside SimpleJWT with `The SECRET_KEY setting must not be empty` —
confusing, and it surfaces at first request rather than at boot.

**Recommendation.** Raise `ImproperlyConfigured` at import when unset, and reject
a key under 50 characters in production (`security.W009` flags the same thing).
Deferred because it touches settings that every branch in flight also touches.

### S6 — No bot protection on login · Low · Deferred

Addressed on `feat/captcha-toggle`.

### Boundaries that were checked and are sound

- **Tenant isolation.** No view trusts a client-supplied club id; scope is
  derived from the URL slug through `resolve_club_scope`. Spot-checked the
  settlements, transactions, bookings, and audit viewsets — every one builds its
  queryset from `scoped_queryset()` before filters run.
- **Out-of-scope rows 404 rather than 403.** Confirmed in the Spine tests; no
  existence leak.
- **Login enumeration.** The generic credential-failure response still holds.
  Worth re-checking after the throttling and CAPTCHA branches land: a 429 or a
  challenge that fired only for existing usernames would reintroduce the oracle.
  Both branches ship a test for exactly that.
- **`SQLQueryStatsMiddleware`** logs no parameters, bodies, or `Authorization`
  headers. Re-verify if log configuration changes under Docker.

---

## Performance

### P1 — No cache backend configured · **High** · Deferred

No `CACHES` setting existed, so Django used the per-process `LocMemCache`.
Nothing depended on it yet, which is why this had no symptom — but it is a
prerequisite for rate limiting, where per-worker counters turn a limit of `N`
into `workers x N`. Fixed on `chore/docker-dev-prod` (and again on the rate-limit
branch, deliberately with identical text so the two merge cleanly).

### P2 — Custody candidate query lacks a covering index · Medium · Deferred

[`apps/settlements/services.py`](../../apps/settlements/services.py) `_calculate`
filters `club` + `settlement_line__isnull=True` + `is_cancelled=False`, and this
is the single hottest financial query — preview, settle, the dashboard, and staff
deactivation all run it.

`Transaction.Meta.indexes` covers `(club, is_cancelled, created)`, so the
`club`/`is_cancelled` half is served, but the unsettled half is a reverse
one-to-one join resolved by anti-join.

**Recommendation.** On PostgreSQL, a partial index:

```python
models.Index(
    fields=["club", "created_at"],
    condition=Q(is_cancelled=False),
    name="txn_club_created_unsettled_idx",
)
```

Measure before adding — `Transaction` already carries twelve indexes, and each
one costs write throughput on the busiest table in the system.

### P3 — Static locations payload is rebuilt per request · Medium · Deferred

`GET /api/v1/egypt-locations/` calls `get_location_payload()` on every request
for data that only changes when the source file does. Cheap to cache once a
shared cache exists (P1). Low urgency — no database is touched.

### P4 — SSL redirect would have broken the deploy gate · Low · Fixed pre-emptively

Enabling `SECURE_SSL_REDIRECT` (S1) collides with the deploy contract:
`scripts/deploy-sloty-backend` polls `http://127.0.0.1:8020/health/` over plain
HTTP with a `Host` header and **requires HTTP 200**. With the redirect on and no
`X-Forwarded-Proto` on that request, Django answers `301` and every deploy fails
its health gate.

**Fixed** with `SECURE_REDIRECT_EXEMPT = [r"^health/$"]`, covered by an
end-to-end test asserting `/health/` returns 200 over plain HTTP while other
paths still redirect.

### Checked and sound

- **N+1 (smoke test only).** Drove the five club-scoped list endpoints with
  `SQL_QUERY_STATS_ENABLED=true` and confirmed the middleware was actually
  emitting (5 `[PERF]` lines captured). Results:

  | Endpoint | Queries | Duplicates | N+1 heuristics |
  | :--- | ---: | ---: | ---: |
  | `courts/` | 6 | 0 | 0 |
  | `bookings/` | 4 | 0 | 0 |
  | `transactions/` | 4 | 0 | 0 |
  | `settlements/` | 4 | 0 | 0 |
  | `audit-logs/` | 4 | 0 | 0 |

  **Read this narrowly.** It ran against a near-empty fixture (one club, five
  courts, no bookings), so it proves the endpoints do not N+1 on *shape*, not
  that they scale. The real guarantee is the existing query-count tests
  (`test_settlement_list_query_count_does_not_grow_with_rows` and siblings),
  which assert counts do not grow with row count and which pass. A load test on
  production-shaped data is still worth doing and has not been done.
- **Lock scope.** `select_for_update(of=("self",))` in the custody path correctly
  locks only `Transaction` rows rather than every joined table — important,
  because a wider lock would serialize a whole club on a busy evening.
- **Load plans.** Every `authorization_config` declares `select_related` /
  `prefetch_related` matching what its serializer reads.

---

## Recommended order

1. S5 (`SECRET_KEY` fail-fast) — small, self-contained.
2. S4 — needs the product decision first.
3. P2 — measure on production-shaped data before adding the index.
4. P3 — after a shared cache is live.
