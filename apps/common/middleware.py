import hashlib
import logging
import re
from collections import defaultdict
from decimal import Decimal
from time import perf_counter
from uuid import UUID

from django.conf import settings
from django.db import connection

logger = logging.getLogger("sloty.sql")

_WHITESPACE_RE = re.compile(r"\s+")
_TRANSACTION_SQL_RE = re.compile(
    r"^(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)(\s|$)",
    re.IGNORECASE,
)
_MAX_STORED_SLOW_QUERIES = 32


def normalize_sql(sql):
    return _WHITESPACE_RE.sub(" ", (sql or "").strip())


def is_transaction_control_sql(sql):
    return bool(_TRANSACTION_SQL_RE.match(normalize_sql(sql)))


def _fingerprint(material):
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:8]


def _params_material(params):
    if params is None:
        return ""
    if isinstance(params, bool):
        return repr(params)
    if isinstance(params, (str, int, float)):
        return repr(params)
    if isinstance(params, Decimal):
        return f"Decimal:{params}"
    if isinstance(params, UUID):
        return f"UUID:{params}"
    if isinstance(params, (bytes, bytearray, memoryview)):
        return f"<{type(params).__name__}:{len(params)}>"
    if hasattr(params, "isoformat") and not isinstance(params, str):
        try:
            return f"{type(params).__name__}:{params.isoformat()}"
        except Exception:
            pass
    if isinstance(params, dict):
        items = sorted(
            (str(key), _params_material(value)) for key, value in params.items()
        )
        return repr(items)
    if isinstance(params, (list, tuple, set, frozenset)):
        return repr(tuple(_params_material(item) for item in params))
    if hasattr(params, "__iter__") and not isinstance(params, (str, bytes, dict)):
        return repr(tuple(_params_material(item) for item in list(params)))
    return repr(params)


def params_fingerprint(params, *, many=False):
    try:
        material = f"{int(bool(many))}:{_params_material(params)}"
    except Exception:
        material = f"<unhashable:{type(params).__name__}>"
    return _fingerprint(material)


def truncate_sql(sql):
    max_length = int(getattr(settings, "SQL_QUERY_STATS_MAX_SQL_LENGTH", 500))
    normalized = normalize_sql(sql)
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length]}..."


class SQLQueryStats:
    """Collect per-request SQL counts, timing, and duplicate/N+1 heuristics.

    Definitions (request-local, never persisted):
    - queries: execute_wrapper invocations, including transaction control
    - duplicates: distinct SQL+params groups executed more than once
    - duplicate_executions: extra executions beyond the first in those groups
    - repeated_query_shapes: distinct SQL structures executed more than once
    - potential_n_plus_one: SQL structures executed more than once with
      more than one distinct parameter fingerprint (heuristic, not certainty)

    Transaction-control statements are counted in query_count and db time
    but are excluded from duplicate/shape/N+1 heuristics.
    """

    def __init__(self):
        self.query_count = 0
        self.sql_ms = 0.0
        self.slow_queries = []
        self._shape_sql = {}
        self._shape_counts = defaultdict(int)
        self._shape_param_counts = defaultdict(lambda: defaultdict(int))
        self._duplicate_groups = None
        self._repeated_shape_groups = None

    def __call__(self, execute, sql, params, many, context):
        started_at = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            duration_ms = (perf_counter() - started_at) * 1000
            self.query_count += 1
            self.sql_ms += duration_ms
            try:
                self._index_query(sql, params, duration_ms, many=many)
            except Exception:
                logger.debug("SQL query stats indexing failed", exc_info=True)

    def _index_query(self, sql, params, duration_ms, *, many=False):
        self._duplicate_groups = None
        self._repeated_shape_groups = None
        normalized = normalize_sql(sql)
        slow_query_ms = getattr(settings, "SQL_QUERY_STATS_SLOW_QUERY_MS", None)
        if slow_query_ms is not None and duration_ms >= float(slow_query_ms):
            self.slow_queries.append((duration_ms, truncate_sql(normalized)))
            if len(self.slow_queries) > _MAX_STORED_SLOW_QUERIES:
                self.slow_queries.sort(key=lambda item: item[0], reverse=True)
                del self.slow_queries[_MAX_STORED_SLOW_QUERIES:]
        if is_transaction_control_sql(normalized):
            return
        shape_fp = _fingerprint(normalized)
        param_fp = params_fingerprint(params, many=many)
        self._shape_sql.setdefault(shape_fp, truncate_sql(normalized))
        self._shape_counts[shape_fp] += 1
        self._shape_param_counts[shape_fp][param_fp] += 1

    def duplicate_groups(self):
        if self._duplicate_groups is None:
            groups = []
            for shape_fp, param_counts in self._shape_param_counts.items():
                for param_fp, count in param_counts.items():
                    if count <= 1:
                        continue
                    groups.append(
                        {
                            "fingerprint": _fingerprint(f"{shape_fp}:{param_fp}"),
                            "occurrences": count,
                            "duplicate_executions": count - 1,
                            "sql": self._shape_sql.get(shape_fp, ""),
                        }
                    )
            groups.sort(key=lambda item: item["occurrences"], reverse=True)
            self._duplicate_groups = groups
        return self._duplicate_groups

    def repeated_shape_groups(self):
        if self._repeated_shape_groups is None:
            groups = []
            for shape_fp, count in self._shape_counts.items():
                if count <= 1:
                    continue
                distinct_params = len(self._shape_param_counts[shape_fp])
                groups.append(
                    {
                        "fingerprint": shape_fp,
                        "occurrences": count,
                        "distinct_params": distinct_params,
                        "potential_n_plus_one": distinct_params > 1,
                        "sql": self._shape_sql.get(shape_fp, ""),
                    }
                )
            groups.sort(key=lambda item: item["occurrences"], reverse=True)
            self._repeated_shape_groups = groups
        return self._repeated_shape_groups

    @property
    def duplicate_count(self):
        return len(self.duplicate_groups())

    @property
    def duplicate_executions(self):
        return sum(group["duplicate_executions"] for group in self.duplicate_groups())

    @property
    def repeated_shape_count(self):
        return len(self.repeated_shape_groups())

    @property
    def potential_n_plus_one_count(self):
        return sum(
            1 for group in self.repeated_shape_groups() if group["potential_n_plus_one"]
        )


def _max_query_samples():
    return max(int(getattr(settings, "SQL_QUERY_STATS_MAX_QUERY_SAMPLES", 5)), 0)


def emit_sql_query_stats(*, method, path, status_code, stats, total_ms):
    verbose = bool(getattr(settings, "SQL_QUERY_STATS_VERBOSE", False))
    warn_query_count = int(getattr(settings, "SQL_QUERY_STATS_WARN_QUERY_COUNT", 1))
    slow_request_ms = float(getattr(settings, "SQL_QUERY_STATS_SLOW_REQUEST_MS", 500))
    sample_limit = _max_query_samples()
    duplicate_groups = stats.duplicate_groups()
    repeated_shape_groups = stats.repeated_shape_groups()
    n_plus_one_groups = [
        group for group in repeated_shape_groups if group["potential_n_plus_one"]
    ]

    logger.info(
        "[PERF] %s %s status=%s queries=%s db=%.2fms total=%.2fms "
        "duplicates=%s repeated_shapes=%s potential_n_plus_one=%s",
        method,
        path,
        status_code,
        stats.query_count,
        stats.sql_ms,
        total_ms,
        len(duplicate_groups),
        len(repeated_shape_groups),
        len(n_plus_one_groups),
    )

    if stats.query_count > warn_query_count:
        logger.warning(
            "[PERF:WARN] queries=%s threshold=%s method=%s path=%s "
            "db=%.2fms total=%.2fms duplicates=%s potential_n_plus_one=%s",
            stats.query_count,
            warn_query_count,
            method,
            path,
            stats.sql_ms,
            total_ms,
            len(duplicate_groups),
            len(n_plus_one_groups),
        )

    for group in duplicate_groups[:sample_limit]:
        if verbose:
            logger.warning(
                "[PERF:DUPLICATE] occurrences=%s duplicate_executions=%s "
                "fingerprint=%s sql=%s",
                group["occurrences"],
                group["duplicate_executions"],
                group["fingerprint"],
                truncate_sql(group["sql"]),
            )
        else:
            logger.warning(
                "[PERF:DUPLICATE] occurrences=%s duplicate_executions=%s "
                "fingerprint=%s",
                group["occurrences"],
                group["duplicate_executions"],
                group["fingerprint"],
            )

    for group in n_plus_one_groups[:sample_limit]:
        if verbose:
            logger.warning(
                "[PERF:N+1?] occurrences=%s fingerprint=%s sql=%s",
                group["occurrences"],
                group["fingerprint"],
                truncate_sql(group["sql"]),
            )
        else:
            logger.warning(
                "[PERF:N+1?] occurrences=%s fingerprint=%s",
                group["occurrences"],
                group["fingerprint"],
            )

    slow_queries = sorted(stats.slow_queries, key=lambda item: item[0], reverse=True)
    for duration_ms, sql in slow_queries[:sample_limit]:
        if verbose:
            logger.warning(
                "[PERF:SLOW_QUERY] %.2fms sql=%s",
                duration_ms,
                truncate_sql(sql),
            )
        else:
            logger.warning("[PERF:SLOW_QUERY] %.2fms", duration_ms)

    if total_ms >= slow_request_ms:
        logger.warning(
            "[PERF:SLOW_REQUEST] %s %s total=%.2fms threshold=%.2fms db=%.2fms",
            method,
            path,
            total_ms,
            slow_request_ms,
            stats.sql_ms,
        )


class SQLQueryStatsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "SQL_QUERY_STATS_ENABLED", False):
            return self.get_response(request)

        stats = SQLQueryStats()
        started_at = perf_counter()
        response = None
        try:
            with connection.execute_wrapper(stats):
                response = self.get_response(request)
            return response
        finally:
            try:
                emit_sql_query_stats(
                    method=getattr(request, "method", ""),
                    path=getattr(request, "path", ""),
                    status_code=(response.status_code if response is not None else 500),
                    stats=stats,
                    total_ms=(perf_counter() - started_at) * 1000,
                )
            except Exception:
                logger.debug("SQL query stats logging failed", exc_info=True)
