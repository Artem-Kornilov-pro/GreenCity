"""
GreenCity API -- FastAPI-приложение: middleware, прогрев корпуса GreenPlan
при старте и /api/health. Эндпоинты -- в пакете api/ по темам.

Бэкенд рассчитан на работу за прокси фронтенда (vite в dev, nginx в
продакшене) внутри сети контейнеров: браузер обращается к API с того же
адреса, что и к сайту, поэтому CORS по умолчанию выключен. Разрешить другие
источники -- CORS_ALLOW_ORIGINS (через запятую). Swagger (/docs) -- если
API_DOCS не равен 0; в продакшене выключен.

Запуск из папки backend/:
    uvicorn main:app --reload --port 8000
"""

import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from api import accounts as accounts_api
from api import editor as editor_api
from api import greenplan as greenplan_api
from api.protection import BodySizeLimitMiddleware, metric_path
from core.concurrency import ServerBusy
from core.paths import ENV_FILE
from greenplan import pattern_corpus
from monitoring import metrics
from monitoring.logging_config import RequestLoggingMiddleware, configure_logging
from storage import db

configure_logging()

API_DESCRIPTION = """
Сервис автоматического проектирования озеленения по чертежам DXF/DWG
с учётом подземных коммуникаций, норм отступов и ассортимента растений Москвы.

**Основной сценарий**

1. `POST /api/parse` (DXF) или `POST /api/parse-dwg` (папка DWG) — чертёж в сцену.
2. `POST /api/greenplan/generate` — озеленение участка по похожим проектам.
3. `POST /api/export-dxf` — исходный чертёж плюс слои результата `NEW_*` и `USER_*`.
4. `POST /api/greenplan/explanations` — объяснение каждой посадки со ссылкой на НПА.
5. `POST /api/greenplan/document` — пояснительная записка в DOCX.

Правки текстом — `POST /api/edit-with-text`. Сохранённые проекты требуют
входа в аккаунт (кнопка **Authorize**, токен из `POST /api/auth/login`).

**Ограничения.** Тяжёлые эндпоинты ограничены по частоте запросов с одного
адреса (429) и по размеру запроса (413); если сервер занят расчётами — 503.
В обоих случаях заголовок `Retry-After` — через сколько секунд повторить.
"""

TAGS = [
    {"name": "Чертежи", "description": "Загрузка DXF и DWG, экспорт результата в DXF."},
    {"name": "GreenPlan", "description": "Автоматическое озеленение участка, объяснения посадок, пояснительная записка."},
    {"name": "Редактор", "description": "Каталог видов и малых форм, правка плана текстом, генерация по сетке."},
    {"name": "Аккаунты", "description": "Регистрация и вход по имени пользователя и паролю."},
    {"name": "Проекты", "description": "Сохранённые проекты пользователя (нужен вход)."},
    {"name": "Служебное", "description": "Проверка работоспособности."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.ensure_indexes()
    # Признаки проектов корпуса считаются в фоне: /api/health должен отвечать
    # сразу, иначе healthcheck пометит контейнер нездоровым.
    app.state.corpus_warmup_task = asyncio.create_task(asyncio.to_thread(pattern_corpus.corpus_characteristics))
    yield


# API_DOCS и CORS_ALLOW_ORIGINS можно задать в .env в корне репозитория.
load_dotenv(ENV_FILE)
API_DOCS = os.environ.get("API_DOCS", "1") != "0"
CORS_ALLOW_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(
    title="GreenCity API",
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "Только просмотр исходного кода", "url": "https://github.com/Artem-Kornilov-pro/GreenCity/blob/main/LICENSE"},
    lifespan=lifespan,
    docs_url="/docs" if API_DOCS else None,
    redoc_url="/redoc" if API_DOCS else None,
    openapi_url="/openapi.json" if API_DOCS else None,
)

# Middleware, добавленный позже, оборачивает добавленные раньше: лимит тела --
# ближе всех к приложению, журнал запросов видит и его отказы.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
if CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-GreenCity-Export", "Content-Disposition"],
    )
# Сцена реального участка -- 1-13 МБ JSON, в gzip в 5-6 раз меньше.
app.add_middleware(GZipMiddleware, minimum_size=1024)
Instrumentator().instrument(app).expose(app, include_in_schema=False)


@app.exception_handler(ServerBusy)
async def server_busy_handler(request: Request, exc: ServerBusy) -> JSONResponse:
    metrics.busy_rejections_total.labels(path=metric_path(request.url.path)).inc()
    return JSONResponse({"detail": exc.detail}, status_code=503, headers={"Retry-After": str(exc.retry_after_s)})


class HealthStatus(BaseModel):
    status: str = "ok"


@app.get("/api/health", tags=["Служебное"], summary="Проверка работоспособности", response_model=HealthStatus)
async def health():
    return HealthStatus()


app.include_router(editor_api.router)
app.include_router(greenplan_api.router)
app.include_router(accounts_api.router)
