"""logging_config.py -- JSON-форматтер, request_id через ContextVar, ASGI
middleware для журнала доступа."""

import json
import logging

import pytest
from logging_config import (
    JsonFormatter,
    RequestLoggingMiddleware,
    bind_request_id,
    configure_logging,
    current_request_id,
    new_request_id,
)


def test_new_request_id_is_short_hex_and_unique():
    a, b = new_request_id(), new_request_id()
    assert a != b
    assert len(a) == 12
    int(a, 16)  # не должно поднять ValueError -- валидный hex


def test_bind_and_current_request_id_round_trip():
    bind_request_id("abc123")
    assert current_request_id() == "abc123"


def test_current_request_id_defaults_to_dash_outside_a_request():
    # ContextVar объявлена с default="-" (см. logging_config.py) -- без
    # bind_request_id и без ранее обработанного запроса это и есть значение,
    # которое увидит любой лог, написанный вне обработки HTTP-запроса.
    assert current_request_id() == "-"


@pytest.fixture(autouse=True)
def _reset_request_id_context():
    """request_id живёt в ContextVar на уровне процесса -- без сброса между
    тестами значение, установленное одним тестом (bind_request_id), протекало
    бы в следующий, делая его результат зависимым от порядка запуска."""
    from logging_config import _request_id_ctx

    token = _request_id_ctx.set("-")
    yield
    _request_id_ctx.reset(token)


def _make_record(msg="hello", level=logging.INFO, extra=None):
    record = logging.LogRecord(name="greencity.test", level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_valid_json_with_core_fields():
    record = _make_record("hello world")
    record.request_id = "req-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "greencity.test"
    assert payload["request_id"] == "req-1"
    assert "ts" in payload


def test_json_formatter_includes_extra_fields():
    record = _make_record("event", extra={"username": "alice", "client_ip": "1.2.3.4"})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["username"] == "alice"
    assert payload["client_ip"] == "1.2.3.4"


def test_json_formatter_defaults_request_id_to_dash_when_absent():
    record = _make_record("no request id set")
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "-"


def test_json_formatter_includes_exception_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record("failed", level=logging.ERROR)
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_json_formatter_serializes_non_json_native_extra_values():
    class Weird:
        def __str__(self):
            return "weird-value"

    record = _make_record("event", extra={"thing": Weird()})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["thing"] == "weird-value"


def test_configure_logging_sets_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()
    try:
        assert logging.getLogger().level == logging.WARNING
        assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    finally:
        configure_logging_reset = logging.getLogger()
        configure_logging_reset.setLevel(logging.WARNING)  # оставить чистым для следующего теста не критично


def test_configure_logging_defaults_to_info_for_unknown_level(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_falls_back_to_info_for_garbage_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "NOT_A_REAL_LEVEL")
    configure_logging()
    assert logging.getLogger().level == logging.INFO


# --- RequestLoggingMiddleware -------------------------------------------------


class _RecordingApp:
    """ASGI-приложение-заглушка: отвечает 200 (или заданным кодом) и
    записывает request_id, который видело во время обработки запроса."""

    def __init__(self, status=200, seen=None):
        self.status = status
        self.seen = seen if seen is not None else []

    async def __call__(self, scope, receive, send):
        self.seen.append(current_request_id())
        await send({"type": "http.response.start", "status": self.status, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _run_asgi(app, scope):
    async def receive():
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def test_middleware_passes_through_non_http_scopes_untouched():
    inner = _RecordingApp()
    middleware = RequestLoggingMiddleware(inner)
    await _run_asgi(middleware, {"type": "lifespan"})
    # Пропускает вызов дальше (lifespan-события должны доходить до
    # приложения), но НЕ генерирует новый request_id -- это только для HTTP.
    assert inner.seen == ["-"]


async def test_middleware_binds_a_request_id_visible_to_the_inner_app():
    inner = _RecordingApp()
    middleware = RequestLoggingMiddleware(inner)
    await _run_asgi(middleware, {"type": "http", "method": "GET", "path": "/api/health", "client": ("1.2.3.4", 1234)})
    assert len(inner.seen) == 1
    assert inner.seen[0] != "-"


async def test_middleware_logs_final_status_code(caplog):
    inner = _RecordingApp(status=404)
    middleware = RequestLoggingMiddleware(inner)
    with caplog.at_level(logging.INFO, logger="greencity.access"):
        await _run_asgi(middleware, {"type": "http", "method": "GET", "path": "/nope", "client": ("1.2.3.4", 1234)})
    assert any(r.http_status == 404 for r in caplog.records)


async def test_middleware_handles_missing_client_info():
    inner = _RecordingApp()
    middleware = RequestLoggingMiddleware(inner)
    # scope без "client" -- сценарий, например, для запросов без TCP-адреса
    await _run_asgi(middleware, {"type": "http", "method": "GET", "path": "/x"})


async def test_middleware_logs_even_when_inner_app_raises():
    class _Raising:
        async def __call__(self, scope, receive, send):
            raise RuntimeError("boom")

    middleware = RequestLoggingMiddleware(_Raising())
    with pytest.raises(RuntimeError):
        await _run_asgi(middleware, {"type": "http", "method": "GET", "path": "/x", "client": ("1.2.3.4", 1)})
