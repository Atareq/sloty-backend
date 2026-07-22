import logging
from time import perf_counter

from django.conf import settings
from django.db import connection

logger = logging.getLogger("sloty.sql")


class SQLQueryStats:
    def __init__(self):
        self.query_count = 0
        self.sql_ms = 0.0
        self.slow_queries = []

    def __call__(self, execute, sql, params, many, context):
        started_at = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            duration_ms = (perf_counter() - started_at) * 1000
            self.query_count += 1
            self.sql_ms += duration_ms
            slow_query_ms = settings.SQL_QUERY_STATS_SLOW_QUERY_MS
            if (
                settings.SQL_QUERY_STATS_VERBOSE
                and slow_query_ms is not None
                and duration_ms >= slow_query_ms
            ):
                self.slow_queries.append((duration_ms, sql))


class SQLQueryStatsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.SQL_QUERY_STATS_ENABLED:
            return self.get_response(request)

        stats = SQLQueryStats()
        started_at = perf_counter()
        with connection.execute_wrapper(stats):
            response = self.get_response(request)

        total_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "[SQL] %s %s status=%s queries=%s sql=%.2fms total=%.2fms",
            request.method,
            request.path,
            response.status_code,
            stats.query_count,
            stats.sql_ms,
            total_ms,
        )
        for duration_ms, sql in stats.slow_queries:
            logger.info("[SQL:SLOW] %.2fms %s", duration_ms, sql)
        return response
