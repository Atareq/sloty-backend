from contextlib import nullcontext
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import RequestFactory

from apps.common.middleware import SQLQueryStats, SQLQueryStatsMiddleware


def test_stats_wrapper_counts_and_times_queries(monkeypatch):
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_VERBOSE", False)
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_SLOW_QUERY_MS", 100)
    stats = SQLQueryStats()

    result = stats(lambda *args: "ok", "SELECT 1", None, False, {})

    assert result == "ok"
    assert stats.query_count == 1
    assert stats.sql_ms >= 0


def test_middleware_does_not_wrap_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_ENABLED", False)
    request = RequestFactory().get("/api/v1/me/")
    get_response = Mock(return_value=Mock(status_code=200))
    middleware = SQLQueryStatsMiddleware(get_response)

    with patch("apps.common.middleware.connection.execute_wrapper") as wrapper:
        response = middleware(request)

    assert response.status_code == 200
    wrapper.assert_not_called()


def test_middleware_wraps_and_logs_summary_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_ENABLED", True)
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_VERBOSE", False)
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_SLOW_QUERY_MS", 100)
    request = RequestFactory().get("/api/v1/me/")
    get_response = Mock(return_value=Mock(status_code=200))
    middleware = SQLQueryStatsMiddleware(get_response)

    with patch(
        "apps.common.middleware.connection.execute_wrapper",
        return_value=nullcontext(),
    ) as wrapper:
        with patch("apps.common.middleware.logger.info") as log_info:
            response = middleware(request)

    assert response.status_code == 200
    wrapper.assert_called_once()
    log_info.assert_called_once()
    assert log_info.call_args.args[:4] == (
        "[SQL] %s %s status=%s queries=%s sql=%.2fms total=%.2fms",
        "GET",
        "/api/v1/me/",
        200,
    )
