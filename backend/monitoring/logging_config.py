"""
Структурированные логи бэкенда: JSON по строке на запись в stdout, с
отдельными полями (уровень, логгер, сообщение, контекст) для системы сбора
логов. Логгеры uvicorn не трогаются -- JSON пишут только логгеры приложения
(greencity.*).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar

# Стандартные поля LogRecord; всё сверх них пришло через extra={...} и
# уходит в JSON как есть.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__.keys()
) | {"message", "asctime"}

# request_id текущего запроса -- ContextVar: асинхронные запросы одного
# процесса перемежаются.
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Уровень для многословных сторонних библиотек: ezdxf при сборке пачки DWG
# пишет предупреждение на каждое поле FIELD -- сотни строк на загрузку.
NOISY_LIBRARY_LEVELS = {"ezdxf": logging.ERROR}


def current_request_id() -> str:
    return _request_id_ctx.get()


def bind_request_id(value: str) -> None:
    _request_id_ctx.set(value)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class _RequestIdFilter(logging.Filter):
    """Добавляет request_id из ContextVar к каждой записи -- так любая строка
    лога, написанная во время обработки запроса (в любом модуле, сколь угодно
    глубоко по стеку вызовов), помечена тем же id, что и итоговая строка
    доступа из RequestLoggingMiddleware -- можно собрать все логи одного
    запроса поиском по id, не имея готовой системы трассировки."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_FIELDS and k != "request_id"}
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Настраивает логирование; вызывается один раз при импорте main.py.
    Уровень -- LOG_LEVEL (по умолчанию INFO).
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    for name, floor in NOISY_LIBRARY_LEVELS.items():
        logging.getLogger(name).setLevel(max(level, floor))


class RequestLoggingMiddleware:
    """ASGI-middleware (быстрее BaseHTTPMiddleware на больших ответах).
    На каждый запрос: request_id в ContextVar, чтобы все логи запроса несли
    один id, и итоговая строка журнала доступа -- метод, путь, код,
    длительность, IP клиента.
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("greencity.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        bind_request_id(request_id)
        started = time.monotonic()
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        client = scope.get("client")
        client_host = client[0] if client else "-"

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            self.logger.info(
                "%s %s -> %s (%.1f ms)",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                duration_ms,
                extra={
                    "http_method": scope.get("method", "-"),
                    "http_path": scope.get("path", "-"),
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_host,
                },
            )
