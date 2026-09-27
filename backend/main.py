"""
GreenCity API -- FastAPI-приложение: middleware, прогрев корпуса GreenPlan
при старте и /api/health. Эндпоинты -- в пакете api/ по темам.

Запуск из папки backend/:
    uvicorn main:app --reload --port 8000
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from api import accounts as accounts_api
from api import editor as editor_api
from api import greenplan as greenplan_api
from greenplan import pattern_corpus
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


app = FastAPI(
    title="GreenCity API",
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "Только просмотр исходного кода", "url": "https://github.com/Artem-Kornilov-pro/GreenCity/blob/main/LICENSE"},
    lifespan=lifespan,
)

# Первым -- чтобы логировать итоговый код ответа и полную длительность запроса.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
# Сцена реального участка -- 1-13 МБ JSON, в gzip в 5-6 раз меньше.
app.add_middleware(GZipMiddleware, minimum_size=1024)
Instrumentator().instrument(app).expose(app, include_in_schema=False)


class HealthStatus(BaseModel):
    status: str = "ok"


# async: синхронные обработчики стоят в общей очереди потоков за тяжёлой
# геометрией, а healthcheck должен отвечать и под нагрузкой.
@app.get("/api/health", tags=["Служебное"], summary="Проверка работоспособности", response_model=HealthStatus)
async def health():
    return HealthStatus()


app.include_router(editor_api.router)
app.include_router(greenplan_api.router)
app.include_router(accounts_api.router)
