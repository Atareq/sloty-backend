# Common — Agent Guide

## Responsibility

`apps/common/` owns cross-cutting infrastructure, standardized error handling, Egypt location choices, search utilities, and performance middleware. It contains no domain-specific business models.

## Shared Infrastructure

### 1. Controlled Egypt Locations ([`apps/common/egypt_locations.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/egypt_locations.py))
- **Source of Truth**: Controlled governorates and cities/centers.
- **Helpers**: `get_governorate_choices()`, `get_all_city_choices()`, `get_city_choices(governorate_code)`.
- **Validation**: `is_valid_governorate()`, `is_valid_city()`, `is_valid_city_for_governorate()`.
- **Public Utility API**: `GET /api/v1/egypt-locations/` returns valid governorates and cities.
- **Invariants**: Do not store free-text governorates/cities; do not create database tables for static locations.

### 2. Standardized Error Handling ([`apps/common/exceptions.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/exceptions.py))
- **Base Exception**: `SlotyAPIException(status_code=..., code=..., message=..., details=...)`. Domain services raise this or subclasses for business rule violations.
- **Exception Handler**: `sloty_exception_handler` configured in `config.settings.base.REST_FRAMEWORK["EXCEPTION_HANDLER"]`.
- **Normalized Response Shapes**:
  - Validation Errors (`HTTP 400`):
    ```json
    {
      "success": false,
      "code": "VALIDATION_ERROR",
      "message": "Primary error message.",
      "field_errors": {
        "field_name": [{"code": "invalid", "message": "Detailed error."}]
      }
    }
    ```
  - Business & Auth Exceptions:
    ```json
    {
      "success": false,
      "code": "SPECIFIC_ERROR_CODE",
      "message": "Descriptive message.",
      "details": {}
    }
    ```
- **Standard Authentication Error Codes**:
  - `SESSION_EXPIRED`: Expired JWT access token.
  - `USER_INACTIVE`: Authenticated user is inactive.
  - `USER_DELETED`: User record no longer exists.
  - `CLUB_ACCESS_REVOKED`: User is authenticated but active membership in selected club was lost.

### 3. Egyptian Phone Number Search ([`apps/common/search.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/search.py))
- Provides `phone_search_variants()` and `customer_phone_search_q()` to handle standard Egyptian phone formatting (`010...`, `+2010...`, spaced digits) consistently across booking and transaction search filters.

### 4. SQL Performance Middleware ([`apps/common/middleware.py`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/middleware.py))
- `SQLQueryStatsMiddleware`: Production-safe query stats logger using `connection.execute_wrapper()`.
- Independent of `DEBUG`. Controlled via environment variables: `SQL_QUERY_STATS_ENABLED`, `SQL_QUERY_STATS_SLOW_QUERY_MS`, `SQL_QUERY_STATS_WARN_QUERY_COUNT`.
- Detects exact duplicates and logs potential N+1 heuristics (`[PERF:N+1?]`). Parameters, bodies, and Authorization headers are never logged.

### 5. Authorization Spine Foundation ([`apps/common/authorization/`](file:///home/tarek/Desktop/sloty/sloty-backend/apps/common/authorization/))

> **Architecture reference (target + migration guideline):**
> [`docs/architecture/security-architecture-v1.md`](file:///home/tarek/Desktop/sloty/sloty-backend/docs/architecture/security-architecture-v1.md)
> Do not duplicate that document here. This section is the **implementation contract** for code under `apps/common/authorization/`. Mismatches with the target doc are transitional — code/tests win for active behavior.

- **RequestAccessContext**: Fact container (`user`, `role`, `club`, `membership`, explicitly targeted `court`, `is_platform_admin`). Zero permissions logic and no loaded Court object for club-wide requests. Forward-compat `profile` / `profile_type` currently alias membership/role — not separate Staff/Owner profile tables.
- **Scope Resolver**: `resolve_club_scope(request, club_slug, court_id=None)` enforces URL club authority, returns cached context on `request.access_context`, raises `CLUB_ACCESS_REVOKED` (403) on revoked/missing membership.
- **Role Matrix & Permission**: `SlotyBasePermission` evaluates centralized `ROLE_PERMISSIONS[role][viewset][action]` with strict default deny. Exposes clean `has_object_permission()` extension hook.
- **ClubScopedViewMixin**: ViewSet mixin attaching context during `perform_authentication` before permission checks.

#### Domain `authorization.py` Policy

| Allowed | Forbidden |
| :--- | :--- |
| True business invariants the matrix cannot express | Re-implementing club/court “WHERE” scoping |
| e.g. `manager_can_change_pricing`, Staff `created_by` visibility, settlement self-approval / collector gates | e.g. a bookings module whose only job is “staff may access assigned court” |

**Rule:** Spine owns **WHERE and WHO**. Domain authorization owns **special business conditions** only.

#### Resource URL Rule (Locked)

When a resource has a security boundary, prefer exposing it in the URL so resolution is deterministic (`URL → resolver → scope`). Prefer court in the path when court scope is required. Do **not** force court into club-only resources (e.g. settlements). Apply on new endpoints and deliberate migrations — do not break existing public contracts casually.

### 6. Authorization Spine v2 Resource Query Contract

The v2 foundation secures model-backed querysets without modifying DRF classes and without migrating any domain automatically.

**Only this `authorization_config` shape is official.** Do not invent a second contract.

#### Scope Keys

- `ResourceScope.NONE` (`"none"`): explicit fail-closed scope; returns `.none()`.
- `ResourceScope.CLUB` (`"club"`): filters by the validated URL club.
- `ResourceScope.COURT` (`"court"`): applies the club filter first, then an explicitly targeted court or the staff member's active court assignment. Owners, managers, and platform admins remain bounded to all court-backed rows inside the selected club when no court is explicitly targeted.
- Future keys are allowed as strings. A future scope requires a declared model path and a same-named fact (or `<scope>_ids`) on the resolved context; absence returns `.none()`.

#### Participating Model Contract

Every model that opts into v2 must expose exactly one configuration mapping:

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
        "court": {"path": "court"},  # or "self" when the model is the court boundary
    },
    "default_scope": "court",  # or "club" / "none"
    "select_related": (),
    "prefetch_related": (),
}
```

Settlement / custody target (when Settlement querysets move to v2):

```python
authorization_config = {
    "scopes": {
        "club": {"path": "club"},
    },
    "default_scope": "club",
    "select_related": (),
    "prefetch_related": (),
}
# Collector filtering remains domain business narrowing — never a Court scope.
```

Rules:

- `path` is an ORM relation traversal from the resource model to the scoped entity. Nested paths such as `booking__club` are valid. The reserved `"self"` path scopes a boundary model such as `Court` by its own primary key. Do not branch on concrete model classes in common authorization code.
- Every non-`NONE` resource declares a `club` path because club isolation is always applied first.
- The default scope must be `none` or a key declared in `scopes`.
- `select_related` and `prefetch_related` are mandatory load plans for every authorized queryset of that model. `Prefetch` objects are supported.
- Missing or malformed configuration raises `ImproperlyConfigured`; common code must never recover by returning `Model.objects.all()`.

#### Generic Resolver and ViewSet Mixin

- `scoped_queryset(access_context, model, scope=...)` owns security boundaries and mandatory relation loading. It always starts from `model._default_manager`, validates the context, applies club first, and only then applies court or a future scope.
- `SlotyScopedResourceMixin` resolves/reuses `request.access_context`, discovers the model from `authorization_model` or the declared `queryset`, and exposes `get_scoped_queryset()` plus the standard `get_queryset()` integration.
- Compose it before DRF's class: `class ExampleViewSet(SlotyScopedResourceMixin, ModelViewSet): ...`. DRF `ModelViewSet` itself remains untouched — unscoped ViewSets may still use plain `ModelViewSet`.
- ViewSets may add relations with `authorization_select_related`, `authorization_prefetch_related`, or their getter hooks. They must not remove mandatory model relations.
- Domain filtering belongs in `filter_scoped_queryset(queryset)` or standard DRF filter backends. Both receive the already-authorized queryset and may only narrow it.
- A participating ViewSet must not override `get_queryset()` to start from a model manager, use a second access context, or accept client-provided tenant IDs.
- Domain migrations remain separate, deliberate tasks. The legacy `ClubAccessContext` and `ClubScopedAccessMixin` stay in place until each domain's behavior and tests are migrated. **Courts** (`Court`, `CourtWorkingHour`) is migrated onto this v2 foundation — see `apps/courts/AGENTS.md` for its concrete `authorization_config` and the one remaining domain-specific helper (`manager_can_change_pricing`) the matrix cannot express. **Transactions** and **Settlements** use spine v1 + domain `authorization.py` for business rules; Settlement v2 queryset target is `default_scope="club"` (never court). All other domains remain on the legacy layer until explicitly migrated.
- **Not a presence/activity tracker**: The Spine (`RequestAccessContext`, `ClubScopedViewMixin`, `SlotyScopedResourceMixin`) intentionally has no request-level side effects beyond authorization/scoping. `ClubMembership.last_sync_at` (offline/PWA sync bookkeeping) is updated only by the dedicated `POST /api/v1/me/sync-heartbeat/` endpoint (`apps/accounts/`), never implicitly by any Spine mixin. Do not add such side effects when migrating further domains.

## Testing

- Test suite: [`tests/common/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/common/).
- Test suites:
  - Infrastructure: [`tests/common/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/common/).
  - Authorization Spine: [`tests/authorization/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/authorization/).
- Key test files:
  - `test_context_and_resolver.py`: Scope resolution, caching, missing access, court non-loading.
  - `test_base_permission_and_matrix.py`: Matrix verification, default deny, custom actions, object permission hook.
  - `test_scoped_resources.py`: Model configuration, club/court/future scoping, fail-closed behavior, relation loading, and DRF ViewSet composition.
  - `test_egypt_locations.py` / `test_locations_api.py`: Choice validation and API endpoint.
  - `test_sql_query_stats_middleware.py`: Middleware counting and threshold logging.
