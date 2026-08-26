import logging
from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.test import RequestFactory

from apps.common.middleware import (
    SQLQueryStats,
    SQLQueryStatsMiddleware,
    emit_sql_query_stats,
    params_fingerprint,
)


class Unreprable:
    def __repr__(self):
        raise TypeError("cannot repr")


def _enabled_settings(monkeypatch, **overrides):
    values = {
        "SQL_QUERY_STATS_ENABLED": True,
        "SQL_QUERY_STATS_VERBOSE": False,
        "SQL_QUERY_STATS_SLOW_QUERY_MS": 100,
        "SQL_QUERY_STATS_WARN_QUERY_COUNT": 1,
        "SQL_QUERY_STATS_SLOW_REQUEST_MS": 500,
        "SQL_QUERY_STATS_MAX_QUERY_SAMPLES": 5,
        "SQL_QUERY_STATS_MAX_SQL_LENGTH": 500,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def _run_query(stats, sql, params=None, duration=0.0):
    def execute(*args, **kwargs):
        return "ok"

    with patch("apps.common.middleware.perf_counter", side_effect=[0, duration / 1000]):
        return stats(execute, sql, params, False, {})


@pytest.fixture
def perf_logs(caplog):
    logger = logging.getLogger("sloty.sql")
    logger.addHandler(caplog.handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    caplog.set_level(logging.INFO, logger="sloty.sql")
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)


def test_stats_wrapper_counts_and_times_queries(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()

    result = _run_query(stats, "SELECT 1", duration=12)

    assert result == "ok"
    assert stats.query_count == 1
    assert stats.sql_ms == pytest.approx(12)


def test_zero_query_request_has_no_duplicates(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()

    assert stats.query_count == 0
    assert stats.duplicate_count == 0
    assert stats.repeated_shape_count == 0
    assert stats.potential_n_plus_one_count == 0


def test_one_query_does_not_warn(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT 1")

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=8,
        )

    assert stats.query_count == 1
    assert "[PERF:WARN]" not in perf_logs.text
    assert "[PERF] GET /api/v1/me/ status=200 queries=1" in perf_logs.text


def test_more_than_one_query_triggers_count_warning(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT 1")
    _run_query(stats, "SELECT 2")

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/clubs/demo/bookings/",
            status_code=200,
            stats=stats,
            total_ms=18,
        )

    assert stats.query_count == 2
    assert "[PERF:WARN]" in perf_logs.text
    assert "queries=2 threshold=1 method=GET path=/api/v1/clubs/demo/bookings/" in (
        perf_logs.text
    )


def test_exact_duplicate_queries_are_detected(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    sql = "SELECT id FROM clubs_club WHERE id = %s"
    _run_query(stats, sql, params=(1,))
    _run_query(stats, sql, params=(1,))
    _run_query(stats, sql, params=(1,))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/clubs/demo/",
            status_code=200,
            stats=stats,
            total_ms=20,
        )

    assert stats.duplicate_count == 1
    assert stats.duplicate_executions == 2
    assert stats.potential_n_plus_one_count == 0
    assert "[PERF:DUPLICATE] occurrences=3 duplicate_executions=2" in perf_logs.text


def test_same_shape_different_params_is_potential_n_plus_one(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    sql = "SELECT id FROM bookings_booking WHERE id = %s"
    _run_query(stats, sql, params=(1,))
    _run_query(stats, sql, params=(2,))
    _run_query(stats, sql, params=(3,))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/clubs/demo/bookings/",
            status_code=200,
            stats=stats,
            total_ms=22,
        )

    assert stats.duplicate_count == 0
    assert stats.repeated_shape_count == 1
    assert stats.potential_n_plus_one_count == 1
    assert "[PERF:N+1?] occurrences=3" in perf_logs.text


def test_unrelated_queries_are_not_duplicates(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT id FROM clubs_club WHERE id = %s", params=(1,))
    _run_query(stats, "SELECT id FROM courts_court WHERE id = %s", params=(1,))

    assert stats.duplicate_count == 0
    assert stats.repeated_shape_count == 0
    assert stats.potential_n_plus_one_count == 0


def test_sql_timing_accumulates(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT 1", duration=10)
    _run_query(stats, "SELECT 2", duration=15)

    assert stats.sql_ms == pytest.approx(25)


def test_slow_query_warning(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch, SQL_QUERY_STATS_SLOW_QUERY_MS=5)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT 1", duration=9)

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=12,
        )

    assert "[PERF:SLOW_QUERY] 9.00ms" in perf_logs.text
    assert "SELECT 1" not in perf_logs.text


def test_slow_request_warning(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch, SQL_QUERY_STATS_SLOW_REQUEST_MS=500)
    stats = SQLQueryStats()

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=812.33,
        )

    assert "[PERF:SLOW_REQUEST] GET /api/v1/me/ total=812.33ms" in perf_logs.text


def test_middleware_does_not_wrap_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "SQL_QUERY_STATS_ENABLED", False)
    request = RequestFactory().get("/api/v1/me/")
    get_response = Mock(return_value=Mock(status_code=200, content=b"ok"))
    middleware = SQLQueryStatsMiddleware(get_response)

    with patch("apps.common.middleware.connection.execute_wrapper") as wrapper:
        with patch("apps.common.middleware.logger.info") as log_info:
            response = middleware(request)

    assert response.status_code == 200
    wrapper.assert_not_called()
    log_info.assert_not_called()


def test_verbose_false_does_not_expose_sql_or_params(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch, SQL_QUERY_STATS_VERBOSE=False)
    stats = SQLQueryStats()
    secret = "super-secret-token"
    sql = "SELECT * FROM accounts_user WHERE password = %s"
    _run_query(stats, sql, params=(secret,))
    _run_query(stats, sql, params=(secret,))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=10,
        )

    assert "SELECT" not in perf_logs.text
    assert secret not in perf_logs.text
    assert "password" not in perf_logs.text


def test_verbose_true_may_show_sanitized_sql_without_params(monkeypatch, perf_logs):
    _enabled_settings(
        monkeypatch,
        SQL_QUERY_STATS_VERBOSE=True,
        SQL_QUERY_STATS_MAX_SQL_LENGTH=80,
    )
    stats = SQLQueryStats()
    secret = "jwt-should-never-appear"
    sql = "SELECT * FROM accounts_user WHERE token = %s"
    _run_query(stats, sql, params=(secret,))
    _run_query(stats, sql, params=(secret,))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=10,
        )

    assert "sql=SELECT * FROM accounts_user WHERE token = %s" in perf_logs.text
    assert secret not in perf_logs.text


def test_params_are_never_printed_for_n_plus_one(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch, SQL_QUERY_STATS_VERBOSE=True)
    stats = SQLQueryStats()
    sql = "SELECT * FROM bookings_booking WHERE customer_phone = %s"
    _run_query(stats, sql, params=("+201012345678",))
    _run_query(stats, sql, params=("+201099999999",))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/clubs/demo/bookings/",
            status_code=200,
            stats=stats,
            total_ms=11,
        )

    assert "+201012345678" not in perf_logs.text
    assert "+201099999999" not in perf_logs.text
    assert "[PERF:N+1?]" in perf_logs.text


def test_unhashable_params_do_not_break_wrapper(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()

    result = _run_query(stats, "SELECT 1", params=(Unreprable(), Decimal("1.5")))

    assert result == "ok"
    assert stats.query_count == 1
    assert params_fingerprint((Unreprable(),)) == params_fingerprint((Unreprable(),))


def test_http_error_responses_still_produce_summary(monkeypatch):
    _enabled_settings(monkeypatch)
    request = RequestFactory().get("/api/v1/missing/")
    original = Mock(status_code=404, content=b"not-found")
    middleware = SQLQueryStatsMiddleware(Mock(return_value=original))

    with patch(
        "apps.common.middleware.connection.execute_wrapper",
        return_value=nullcontext(),
    ):
        with patch("apps.common.middleware.emit_sql_query_stats") as emit:
            response = middleware(request)

    assert response is original
    assert response.status_code == 404
    assert response.content == b"not-found"
    kwargs = emit.call_args.kwargs
    assert kwargs["status_code"] == 404
    assert kwargs["path"] == "/api/v1/missing/"
    assert kwargs["total_ms"] >= 0


def test_error_responses_still_produce_summary(monkeypatch):
    _enabled_settings(monkeypatch)
    request = RequestFactory().get("/api/v1/me/")
    middleware = SQLQueryStatsMiddleware(Mock(side_effect=RuntimeError("boom")))

    with patch(
        "apps.common.middleware.connection.execute_wrapper",
        return_value=nullcontext(),
    ):
        with patch("apps.common.middleware.emit_sql_query_stats") as emit:
            with pytest.raises(RuntimeError, match="boom"):
                middleware(request)

    emit.assert_called_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["status_code"] == 500
    assert kwargs["path"] == "/api/v1/me/"


def test_middleware_does_not_change_response(monkeypatch):
    _enabled_settings(monkeypatch)
    request = RequestFactory().get("/api/v1/me/")
    original = Mock(status_code=201, content=b"body")
    middleware = SQLQueryStatsMiddleware(Mock(return_value=original))

    with patch(
        "apps.common.middleware.connection.execute_wrapper",
        return_value=nullcontext(),
    ):
        with patch("apps.common.middleware.emit_sql_query_stats"):
            response = middleware(request)

    assert response is original
    assert response.status_code == 201
    assert response.content == b"body"


def test_middleware_wraps_and_logs_summary_when_enabled(monkeypatch):
    _enabled_settings(monkeypatch)
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
    assert log_info.call_args.args[0].startswith("[PERF] %s %s status=%s queries=%s")


def test_transaction_control_sql_is_not_treated_as_n_plus_one(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    _run_query(stats, "SAVEPOINT %s", params=("s1",))
    _run_query(stats, "SAVEPOINT %s", params=("s2",))
    _run_query(stats, "RELEASE SAVEPOINT %s", params=("s1",))
    _run_query(stats, "COMMIT")

    assert stats.query_count == 4
    assert stats.duplicate_count == 0
    assert stats.potential_n_plus_one_count == 0


def test_unusual_param_types_do_not_break_fingerprinting(monkeypatch):
    _enabled_settings(monkeypatch)
    stats = SQLQueryStats()
    params = (
        uuid4(),
        Decimal("12.50"),
        datetime(2026, 5, 20, 9, 0),
        date(2026, 5, 20),
        {"court_id": 3},
        b"\x00\x01",
        {1, 2},
    )

    result = _run_query(stats, "SELECT 1 WHERE x IN %s", params=params)

    assert result == "ok"
    assert stats.query_count == 1
    assert params_fingerprint(params) == params_fingerprint(params)


def test_duplicate_log_lines_are_capped_by_sample_limit(monkeypatch, perf_logs):
    _enabled_settings(monkeypatch, SQL_QUERY_STATS_MAX_QUERY_SAMPLES=1)
    stats = SQLQueryStats()
    _run_query(stats, "SELECT a FROM t WHERE id = %s", params=(1,))
    _run_query(stats, "SELECT a FROM t WHERE id = %s", params=(1,))
    _run_query(stats, "SELECT b FROM t WHERE id = %s", params=(2,))
    _run_query(stats, "SELECT b FROM t WHERE id = %s", params=(2,))

    with perf_logs.at_level(logging.INFO, logger="sloty.sql"):
        emit_sql_query_stats(
            method="GET",
            path="/api/v1/me/",
            status_code=200,
            stats=stats,
            total_ms=10,
        )

    assert perf_logs.text.count("[PERF:DUPLICATE]") == 1
    assert stats.duplicate_count == 2
