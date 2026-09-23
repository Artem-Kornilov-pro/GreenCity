#!/usr/bin/env python3
"""
FastAPI-бэкенд для веб-редактора озеленения. Эндпоинты:
    POST /api/parse             -- DXF -> JSON-сцена (через parser/parse_dxf.py)
    POST /api/parse-dwg         -- батч из нескольких .dwg (папка проекта) ->
                                    JSON-сцена (issue #50): каждый файл сначала
                                    конвертируется в DXF через dwg2dxf/LibreDWG
                                    (dwg_batch_converter.py), результаты
                                    сливаются в один документ и парсятся тем же
                                    parser/parse_dxf.py, что и /api/parse.
                                    Файлы, которые не удалось сконвертировать,
                                    не роняют весь запрос -- попадают в
                                    dwgConversionWarnings в ответе.
    POST /api/generate-greenery -- JSON-сцена (+ опциональные query-параметры
                                    species/grid_spacing_m/min_tree_spacing_m/
                                    include_trees/include_bushes/
                                    bush_grid_spacing_m/min_bush_spacing_m/
                                    include_lawn/lawn_patch_size_m)
                                    -> JSON-сцена с добавленными деревьями,
                                    группами кустов и газоном.
                                    Полное описание параметров и алгоритма --
                                    backend/README.md.
    POST /api/edit-with-text    -- правка плана текстом через LLM (text_editor/service.py)
    POST /api/export-dxf        -- JSON-сцена -> файл .dxf (export_dxf.py; ТЗ:
                                    "итоговый план должен экспортироваться
                                    обратно в формат DXF")
    POST /api/greenplan/generate -- автоозеленение по прошлым проектам
                                    (GreenPlan, issue #23): сцена -> сцена с
                                    новыми объектами + решения по зонам +
                                    список нарушений норм + ассортимент.
                                    Быстро (доли секунды) и без LLM.
    POST /api/greenplan/report  -- текст-объяснение решений (Этап 6) по
                                    списку из /api/greenplan/generate, через
                                    локальную LLM (mistral:7b/Ollama, ~30 с,
                                    отдельно, чтобы не блокировать генерацию)
    POST /api/greenplan/document -- пояснительная записка в DOCX по
                                    результату /api/greenplan/generate
                                    (greenplan/document.py): решения с
                                    происхождением, ведомость по форме 9
                                    ГОСТ 21.508, проверка норм
    POST /api/auth/register,
    POST /api/auth/login        -- логин/пароль, без почты (auth.py, projects.py);
                                    выдают access- и refresh-токен (см. auth.py)
    POST /api/auth/refresh      -- новый access-токен по refresh-токену
    POST /api/auth/logout       -- отозвать refresh-токен (сессию в Redis)
    GET/POST/PUT/DELETE
         /api/projects[/{id}]   -- сохранённые проекты пользователя, не больше
                                    projects.MAX_PROJECTS_PER_USER на аккаунт,
                                    в MongoDB (db.py). Требуют access-токен --
                                    гостевой режим работает без них: DXF/
                                    генерация/правка текстом выше эндпоинтов
                                    проектов не касаются.
    GET  /metrics               -- метрики Prometheus (prometheus-fastapi-
                                    instrumentator + metrics.py), см.
                                    observability/README.md

Сами эндпоинты -- в пакете api/ по темам: api/editor.py (разбор DXF/DWG,
каталог, генерация по сетке, правка текстом, экспорт DXF), api/greenplan.py,
api/accounts.py (аккаунты и проекты). Здесь -- приложение, middleware,
прогрев корпуса при старте и /api/health.

Запуск (из папки backend/, в venv с requirements.txt из корня проекта):
    uvicorn main:app --reload --port 8000
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from api import accounts as accounts_api
from api import editor as editor_api
from api import greenplan as greenplan_api
from greenplan import pattern_corpus
from monitoring.logging_config import RequestLoggingMiddleware, configure_logging
from storage import db

# Полное логирование приложения -- структурированные JSON-строки в stdout,
# по одной на запись, с request_id и уровнем через LOG_LEVEL (см.
# logging_config.py). Логгеры самого uvicorn (uvicorn.access/uvicorn.error)
# настроены с propagate=False и этой конфигурацией не затрагиваются -- их
# собственный формат вывода остаётся как есть.
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Индексы MongoDB создаются здесь, а не при импорте db.py: у асинхронного
    # клиента нет операций до работающего event loop (см. db.ensure_indexes).
    await db.ensure_indexes()
    # Прогрев retrieval-корпуса GreenPlan (backend/greenplan/pattern_corpus.py) --
    # первый вызов corpus_characteristics() парсит уже 14 реальных DXF (было
    # 9 -- 5 добавлены из new-convert, среди них файлы на единицы мегабайт),
    # а @lru_cache сам по себе не защищает от того, что НЕСКОЛЬКО первых
    # одновременных запросов к /api/greenplan/generate до прогрева каждый
    # начнут парсить все 14 файлов заново, не дожидаясь друг друга --
    # реальный вклад в нестабильность под нагрузкой сразу после старта
    # контейнера.
    #
    # ВАЖНО: create_task, а не await -- на 14 файлах прогрев занял ~15.5с на
    # обычной машине разработчика (было ~7с на 9), а на CI-раннере (2 vCPU,
    # ещё и делит его с mongo/redis, поднимающимися в то же время) вышел за
    # бюджет healthcheck (start_period 10с + retries 5 x interval 10с = 60с)
    # -- реальный сбой docker-build в CI после того, как корпус вырос до 14:
    # `await` здесь блокировал ВЕСЬ `yield`, то есть GET /api/health не
    # отвечал вообще, пока прогрев не закончится, и compose помечал
    # контейнер unhealthy раньше, чем FastAPI успевал начать отвечать.
    # create_task запускает прогрев в фоне и сразу отдаёт yield -- health
    # отвечает немедленно, а cold-start thundering herd (ради которого
    # прогрев вообще нужен) остаётся только на первые секунды после старта,
    # не на всё время, пока контейнер считается живым.
    # Ссылка на task сохраняется на app.state -- иначе не привязанный ни к
    # чему Task рискует быть собран сборщиком мусора до завершения (известная
    # ловушка asyncio.create_task без сохранённой ссылки).
    app.state.corpus_warmup_task = asyncio.create_task(asyncio.to_thread(pattern_corpus.corpus_characteristics))
    yield


app = FastAPI(title="GreenCity API", lifespan=lifespan)

# Добавлен ДО CORSMiddleware намеренно: Starlette оборачивает middleware в
# порядке добавления так, что первый добавленный оказывается САМЫМ внешним
# слоем -- значит именно этот увидит финальный код ответа (уже после CORS) и
# посчитает полную длительность запроса, включая работу остальных middleware.
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # прототип: фронтенд может стучаться с любого dev-порта
    allow_methods=["*"],
    allow_headers=["*"],
)

# Стандартные HTTP-метрики (запросы/с, латентность по хендлеру и коду ответа,
# запросы в процессе обработки) -- без ручной разметки каждого эндпоинта.
# Бизнес-метрики (сколько DXF распарсено, сколько озеленения сгенерировано и
# т.п.) -- отдельно, см. metrics.py, они увеличиваются прямо в теле нужных
# эндпоинтов ниже. .expose(app) сам регистрирует GET /metrics.
Instrumentator().instrument(app).expose(app)


# async def, хотя внутри нечего ждать: синхронный обработчик FastAPI уводит в
# пул потоков, а тот под нагрузкой занят геометрией (/api/parse,
# /api/generate-greenery, /api/export-dxf -- все синхронные и надолго). В
# нагрузочном тесте на 100 пользователей этот эндпоинт отвечал в среднем 25 с
# при максимуме в 113 с -- не потому, что "status: ok" долго считается, а
# потому что запрос стоял в очереди за свободным потоком. Здесь это особенно
# больно: docker-compose healthcheck дёргает именно /api/health с таймаутом 3 с,
# то есть backend помечался бы нездоровым ровно в пик нагрузки, когда он на
# самом деле жив и работает.
@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.include_router(editor_api.router)
app.include_router(greenplan_api.router)
app.include_router(accounts_api.router)
