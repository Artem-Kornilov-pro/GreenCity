"""
Защита тяжёлых эндпоинтов: разбор чертежей, GreenPlan, экспорт, правка
текстом и генерация по сетке.

* Лимит частоты по IP клиента (rate_limit): фиксированное окно в Redis. Если
  Redis недоступен, лимит не действует (fail open, как и у входа).
* Лимит размера тела (BodySizeLimitMiddleware): загрузка DXF и DWG -- до
  MAX_UPLOAD_MB, остальные запросы -- до MAX_BODY_MB. Больше -- 413 ещё до
  разбора тела.
* Очередь расчётов (core/concurrency.py): переполнена -- 503 с Retry-After.

IP клиента за прокси фронтенда берётся из X-Forwarded-For: uvicorn
запускается с --proxy-headers (backend/Dockerfile), а сам бэкенд доступен
только из сети контейнеров.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request

from core.paths import ENV_FILE
from monitoring import metrics
from storage import cache

# Настройки ниже можно задать в .env в корне репозитория (см. .env.example).
load_dotenv(ENV_FILE)

WINDOW_S = 600
# Запросов с одного IP за WINDOW_S по группам эндпоинтов. С запасом на
# обычную работу: один запуск GreenPlan из редактора -- это генерация, текст,
# записка и два файла объяснений.
RATE_LIMITS = {
    "parse": 30,
    "parse_dwg": 10,
    "greenplan": 60,
    "llm": 30,
    "export": 30,
    "generate": 30,
}
# Множитель всех лимитов: 0 -- выключить (нагрузочные прогоны).
RATE_LIMIT_MULTIPLIER = float(os.environ.get("RATE_LIMIT_MULTIPLIER", "1"))

MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "200"))
MAX_BODY_MB = float(os.environ.get("MAX_BODY_MB", "64"))
MAX_DWG_FILES = int(os.environ.get("MAX_DWG_FILES", "60"))
UPLOAD_PATHS = frozenset({"/api/parse", "/api/parse-dwg"})
HEAVY_PATHS = frozenset(
    {
        "/api/parse",
        "/api/parse-dwg",
        "/api/generate-greenery",
        "/api/edit-with-text",
        "/api/export-dxf",
        "/api/greenplan/generate",
        "/api/greenplan/report",
        "/api/greenplan/document",
        "/api/greenplan/explanations",
    }
)

HEAVY_RESPONSES = {
    413: {"description": "Тело запроса больше лимита."},
    429: {"description": "Слишком много запросов с этого адреса, заголовок Retry-After -- через сколько секунд повторить."},
    503: {"description": "Сервер занят расчётами, заголовок Retry-After -- через сколько секунд повторить."},
}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def metric_path(path: str) -> str:
    """Путь для метки метрики: тяжёлые -- как есть, прочие -- одной меткой,
    чтобы id в путях не плодили временные ряды."""
    return path if path in HEAVY_PATHS else "other"


def rate_limit(group: str):
    """Зависимость эндпоинта: не больше RATE_LIMITS[group] запросов с IP за WINDOW_S."""

    async def check(request: Request) -> None:
        limit = int(RATE_LIMITS[group] * RATE_LIMIT_MULTIPLIER)
        if limit <= 0:
            return
        if not await cache.check_rate_limit(f"rl:{group}:{client_ip(request)}", limit, WINDOW_S):
            metrics.rate_limit_blocks_total.labels(endpoint=group).inc()
            raise HTTPException(
                429,
                f"Слишком много запросов с этого адреса. Повторите через {WINDOW_S // 60} минут.",
                headers={"Retry-After": str(WINDOW_S)},
            )

    return Depends(check)


class BodySizeLimitMiddleware:
    """413, если тело запроса больше лимита: по Content-Length сразу, без
    него -- по мере чтения тела."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        limit_mb = MAX_UPLOAD_MB if path in UPLOAD_PATHS else MAX_BODY_MB
        limit = int(limit_mb * 1024 * 1024)
        detail = f"Запрос больше {limit_mb:g} МБ."

        length = dict(scope["headers"]).get(b"content-length")
        if length is not None:
            if not length.isdigit():
                await _send_error(send, 400, "Некорректный заголовок Content-Length.")
                return
            if int(length) > limit:
                metrics.body_too_large_total.labels(path=metric_path(path)).inc()
                await _send_error(send, 413, detail)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    metrics.body_too_large_total.labels(path=metric_path(path)).inc()
                    raise HTTPException(413, detail)
            return message

        await self.app(scope, limited_receive, send)


async def _send_error(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
