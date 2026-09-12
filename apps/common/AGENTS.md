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

## Testing

- Test suite: [`tests/common/`](file:///home/tarek/Desktop/sloty/sloty-backend/tests/common/).
- Key test files:
  - `test_egypt_locations.py` / `test_locations_api.py`: Choice validation and API endpoint.
  - `test_sql_query_stats_middleware.py`: Middleware counting and threshold logging.
