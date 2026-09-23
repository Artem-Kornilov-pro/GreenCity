"""
Полное логирование бэкенда: структурированные (JSON, по одной строке на
запись) логи в stdout вместо голого текста -- в Docker/проде их читает не
человек глазами, а система сбора логов (пусть в этом проекте её и нет --
формат готов к ней уже сейчас), которой нужны отдельные поля (уровень,
логгер, сообщение, доп. контекст), а не одна строка текста для grep.

Не добавляем стороннюю библиотеку форматирования (python-json-logger и т.п.)
-- десяток строк своего форматтера читаются проще, чем ещё одна зависимость
ради того же результата.

Логгер uvicorn (uvicorn.access/uvicorn.error) этот модуль не трогает -- у
него `propagate=False` (см. main.py, комментарий рядом с basicConfig раньше),
собственный формат вывода uvicorn остаётся как есть и продолжает быть полезен
сам по себе; JSON получают только логгеры приложения (`greencity.*`).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar

# Поля LogRecord, которые логгер создаёт сам по себе (см. logging.LogRecord) --
# всё, что сверх этого списка, попало через logger.info(..., extra={...}) и
# должно уйти в JSON как есть, а не потеряться молча.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__.keys()
) | {"message", "asctime"}

# request_id текущего запроса -- ContextVar, а не глобальная переменная:
# ASGI-обработчики одного процесса перемежаются между запросами (async),
# обычная переменная модуля путала бы id разных запросов друг с другом.
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


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
    """Вызывается один раз при импорте main.py, до создания FastAPI-приложения
    -- заменяет собой прежний `logging.basicConfig(format="...")`.

    Уровень логов -- через LOG_LEVEL (по умолчанию INFO), чтобы в проде можно
    было приглушить до WARNING без правки кода, а при отладке -- поднять до
    DEBUG в .env/docker-compose.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]


class RequestLoggingMiddleware:
    """ASGI-middleware (не BaseHTTPMiddleware -- тот оборачивает каждый ответ
    в поток и на больших телах ответа, например DXF, заметно медленнее чистого
    ASGI-подхода) -- на каждый HTTP-запрос:
    1) генерирует request_id и кладёт его в ContextVar (bind_request_id),
       чтобы все логи, написанные при обработке ЭТОГО запроса в любом модуле
       бэкенда, несли тот же id;
    2) по завершении пишет одну итоговую строку с методом/путём/кодом ответа/
       длительностью/IP клиента -- журнал доступа для аудита и разбора
       инцидентов, отдельно от метрик Prometheus (те -- для графиков и
       алертов, а не для "кто и когда").
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
