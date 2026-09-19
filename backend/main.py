#!/usr/bin/env python3
"""
FastAPI-бэкенд для веб-редактора озеленения. Эндпоинты:
    POST /api/parse             -- DXF -> JSON-сцена (через parser/parse_dxf.py)
    POST /api/generate-greenery -- JSON-сцена (+ опциональные query-параметры
                                    species/grid_spacing_m/min_tree_spacing_m/
                                    include_trees/include_bushes/
                                    bush_grid_spacing_m/min_bush_spacing_m/
                                    include_lawn/lawn_patch_size_m)
                                    -> JSON-сцена с добавленными деревьями,
                                    группами кустов и газоном.
                                    Полное описание параметров и алгоритма --
                                    backend/README.md.
    POST /api/edit-with-text    -- правка плана текстом через LLM (llm_editor.py)
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

Запуск (из папки backend/, в venv с requirements.txt из корня проекта):
    uvicorn main:app --reload --port 8000
"""

import asyncio
import io
import logging
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import cache
import db
import metrics
import pattern_corpus
import projects as projects_service
from assortment_report import AssortmentRow, summarize_assortment
from auth import AuthError, CurrentUser, decode_token, refresh_access_token, require_user
from building_setbacks import compute_building_setbacks
from decision_report import DecisionReportUnavailable, generate_report
from deterministic_placement import generate_for_scene
from export_dxf import scene_to_dxf
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from greenery_generator import (
    DEFAULT_BUSH_GRID_SPACING_M,
    DEFAULT_GRID_SPACING_M,
    DEFAULT_LAWN_PATCH_SIZE_M,
    DEFAULT_MIN_BUSH_SPACING_M,
    DEFAULT_MIN_TREE_SPACING_M,
    DEFAULT_TREE_SPECIES,
    MAX_ALLOWED_GRID_SPACING_M,
    MAX_ALLOWED_LAWN_PATCH_SIZE_M,
    MIN_ALLOWED_GRID_SPACING_M,
    MIN_ALLOWED_LAWN_PATCH_SIZE_M,
    generate_bushes,
    generate_lawn,
    generate_trees,
)
from llm_editor import (
    LlmError,
    LlmNotConfiguredError,
    TextEditRequest,
    TextEditResult,
    edit_scene_with_text,
)
from logging_config import RequestLoggingMiddleware, configure_logging
from pattern_assignment import ZoneAssignment
from plant_catalog import CatalogItem, catalog_by_id, load_catalog
from projects import NotFoundError, ProjectsError
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, TypeAdapter
from pymongo.errors import PyMongoError
from schemas import Scene
from schemas_auth import (
    AccessTokenResponse,
    AuthResponse,
    LoginRequest,
    MeResponse,
    Project,
    ProjectCreate,
    ProjectSummary,
    ProjectUpdate,
    RefreshRequest,
    RegisterRequest,
)
from violation_report import Violation, find_violations

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))
from parse_dxf import parse_dxf_file  # noqa: E402

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
    # Прогрев retrieval-корпуса GreenPlan (backend/pattern_corpus.py) --
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


_CATALOG_ADAPTER = TypeAdapter(list[CatalogItem])
CATALOG_CACHE_KEY = "catalog:v1"
# Коротко: при большом числе пользователей это снимает с диска повторное
# чтение/сборку каталога (базовый список + catalog_generated.json) на каждый
# запрос, но и не должно надолго прятать только что сконвертированный пак
# моделей (см. plant_catalog.py -- он читает catalog_generated.json заново
# при каждом вызове load_catalog() именно ради того, чтобы новый пак был
# виден без рестарта; TTL здесь короче обычного кэша, чтобы это свойство не
# терялось надолго).
CATALOG_CACHE_TTL_S = 30


@app.get("/api/catalog", response_model=list[CatalogItem])
async def get_catalog():
    """Каталог типовых посадок и МАФ (plant_catalog.py).

    Единый источник правды: фронтенд строит по нему панель "Добавить объект"
    и выбирает, чем рисовать объект (моделью .glb или примитивом-заглушкой),
    а генератор/будущий LLM-агент -- что вообще можно ставить и с каким
    нормативным отступом. Добавление новых видов не требует правок кода:
    достаточно дописать запись в plant_catalog.CATALOG (или сконвертировать
    пак моделей -- tools/convert_models.mjs) и положить .glb в
    frontend/public/models/.

    Кэшируется в Redis на CATALOG_CACHE_TTL_S (cache.py) -- без Redis просто
    читается напрямую каждый раз, как и раньше.
    """
    cached = await cache.get_cached(CATALOG_CACHE_KEY)
    if cached is not None:
        return _CATALOG_ADAPTER.validate_json(cached)
    catalog = load_catalog()
    await cache.set_cached(CATALOG_CACHE_KEY, _CATALOG_ADAPTER.dump_json(catalog).decode("utf-8"), CATALOG_CACHE_TTL_S)
    return catalog


# Синхронный def, а не async: ezdxf-парсинг -- блокирующий CPU/IO-занятый
# код, а не что-то, что можно await. FastAPI сам уводит синхронный обработчик
# в пул потоков (тот же приём, что и в edit_with_text ниже) -- раньше
# эндпоинт был объявлен async def, но внутри всё равно блокировал event loop
# на время parse_dxf_file, просто не было видно по сигнатуре. file.file --
# обычный синхронный file-like объект (Starlette кладёт туда
# SpooledTemporaryFile), поэтому и .read() здесь без await.
@app.post("/api/parse")
def parse_dxf_endpoint(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "Ожидается файл .dxf")

    data = file.file.read()
    # delete=False + ручной unlink в finally, а не delete=True: на Windows файл,
    # открытый через NamedTemporaryFile, залочен для повторного открытия по
    # имени, пока не закрыт хендл -- ezdxf.readfile(tmp.name) ниже падал бы с
    # PermissionError. На Linux/Mac (delete=True) работало без этой возни.
    # noqa SIM115: контекстный менеджер здесь и не нужен -- закрыть файл до
    # чтения обязательно (см. выше про Windows), а удаление делает finally ниже.
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)  # noqa: SIM115
    try:
        tmp.write(data)
        tmp.close()
        try:
            scene = parse_dxf_file(tmp.name)
        except Exception as e:
            metrics.dxf_parse_errors_total.inc()
            logging.getLogger("greencity.parse").warning("не удалось разобрать %r: %s", file.filename, e)
            raise HTTPException(400, f"Не удалось разобрать DXF: {e}") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    scene["buildingSetbacks"] = compute_building_setbacks(scene.get("objects", []))

    metrics.dxf_parses_total.inc()
    logging.getLogger("greencity.parse").info(
        "разобран %r: %d объектов, %d зон",
        file.filename,
        len(scene.get("objects", [])),
        len(scene.get("restrictions", [])),
    )

    return scene


# Тоже синхронный def -- по той же причине, что и /api/parse выше: внутри
# только блокирующая геометрия (shapely), ни одного await. Раньше сигнатура
# была async def без единого await внутри -- классический анти-паттерн,
# который на каждый вызов подвешивал event loop на всё время генерации.
@app.post("/api/generate-greenery", response_model=Optional[Scene])
def generate_greenery(
    scene: Scene,
    species: Optional[str] = Query(
        default=None,
        description=(
            "Вид дерева (например 'Липа мелколистная'). Влияет на отступы "
            "от ограничений, ЕСЛИ вид описан в "
            "setback_norms.SPECIES_SETBACK_OVERRIDES; для неизвестных видов "
            "используется общая норма для дерева. По умолчанию -- "
            f"{DEFAULT_TREE_SPECIES!r}."
        ),
    ),
    grid_spacing_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_GRID_SPACING_M,
        le=MAX_ALLOWED_GRID_SPACING_M,
        description=(
            "Шаг регулярной сетки кандидатных точек посадки, в метрах. "
            f"Меньше -- гуще посадка. По умолчанию {DEFAULT_GRID_SPACING_M} м. "
            f"Допустимый диапазон: {MIN_ALLOWED_GRID_SPACING_M}-{MAX_ALLOWED_GRID_SPACING_M} м."
        ),
    ),
    min_tree_spacing_m: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Минимальное расстояние между стволами уже отобранных "
            f"сгенерированных деревьев, в метрах. По умолчанию {DEFAULT_MIN_TREE_SPACING_M} м."
        ),
    ),
    include_trees: bool = Query(default=True, description="Добавлять сгенерированные деревья."),
    include_bushes: bool = Query(default=True, description="Добавлять сгенерированные группы кустов."),
    bush_grid_spacing_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_GRID_SPACING_M,
        le=MAX_ALLOWED_GRID_SPACING_M,
        description=(
            "Шаг сетки кандидатных ЦЕНТРОВ групп кустов, в метрах -- отдельно от grid_spacing_m у "
            f"деревьев, у кустов он обычно меньше. По умолчанию {DEFAULT_BUSH_GRID_SPACING_M} м."
        ),
    ),
    min_bush_spacing_m: Optional[float] = Query(
        default=None,
        ge=0.0,
        description=(
            "Минимальное расстояние между ЦЕНТРАМИ групп кустов (не между кустами внутри одной "
            f"группы -- они специально стоят вплотную, см. generate_bushes). По умолчанию {DEFAULT_MIN_BUSH_SPACING_M} м."
        ),
    ),
    include_lawn: bool = Query(default=True, description="Заполнить оставшуюся свободную площадь плиткой газона."),
    lawn_patch_size_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_LAWN_PATCH_SIZE_M,
        le=MAX_ALLOWED_LAWN_PATCH_SIZE_M,
        description=(
            f"Размер стороны плитки газона, в метрах. По умолчанию {DEFAULT_LAWN_PATCH_SIZE_M} м. "
            f"Допустимый диапазон: {MIN_ALLOWED_LAWN_PATCH_SIZE_M}-{MAX_ALLOWED_LAWN_PATCH_SIZE_M} м."
        ),
    ),
):
    """
    Автоматически расставить озеленение на сцене: деревья, группы кустов и
    газон (ТЗ п.16-17), в этом порядке -- каждый следующий шаг видит объекты,
    добавленные предыдущим, как уже занятое место (см. докстринг
    greenery_generator.py про порядок вызова).

    Вызывается с фронтенда по кнопке "Сгенерировать растительность
    автоматически" (frontend/src/App.tsx) -- туда уходит текущая сцена
    (та же структура, что отдаёт /api/parse, плюс любые правки, которые
    пользователь уже внёс вручную: подвинутые/удалённые/добавленные объекты),
    а фронтенд ожидает в ответ сцену той же формы обратно и полностью ею
    заменяет текущее состояние.

    Деревья -- детерминированным demo-генератором по сетке, см. docstring
    generate_trees(). Кусты -- группами по несколько штук вплотную (п.17 ТЗ:
    "меньшие расстояния и группировка"), см. generate_bushes(). Газон --
    сплошными плитками на оставшейся свободной площади, см. generate_lawn().
    Все три сажают только внутри явных зон озеленения (severity == "allowed",
    газон из исходного DXF), избегая forbidden И warning зон (парковка/
    дорожки/сети) с отступом по виду посадки -- подробности и обоснование см.
    в greenery_generator.py::_keep_out_shapes/_raw_zone_shapes.

    Существующие объекты пользователя (здания, фонари, лавки, вручную
    расставленные деревья/кусты) не трогаем -- только добавляем новые в
    scene.objects, остальные поля сцены (boundary/restrictions/windows/
    canopies/meta) возвращаем как есть.

    include_trees/include_bushes/include_lawn позволяют сгенерировать только
    часть озеленения за один вызов (например, добавить кустов на уже готовую
    сцену с деревьями). Остальные параметры -- через query string, НЕ часть
    тела Scene (контракт тела менять не хотелось, раз он уже согласован с
    фронтендом); не переданы -- берутся дефолты из greenery_generator.py.
    Полное описание см. в backend/README.md.

    ПРИМЕЧАНИЕ по контракту: раньше эндпоинт всегда возвращал None ("функция
    ещё не готова", см. api.ts/App.tsx на фронте). Теперь алгоритм реализован,
    поэтому возвращаем реальную Scene всегда, даже если новых объектов
    добавить некуда (пустая допустимая площадь) -- это легитимный результат
    работы, а не "не реализовано". None пока оставлен в response_model на
    случай будущих сценариев без места для растений, которые лучше явно
    отличать от "добавили 0 объектов"; сейчас функция его не возвращает.
    """
    generated_count = 0

    if include_trees:
        new_trees = generate_trees(
            scene,
            species=species,
            grid_spacing_m=grid_spacing_m,
            min_tree_spacing_m=min_tree_spacing_m,
        )
        scene.objects = [*scene.objects, *new_trees]
        metrics.greenery_generated_total.labels(object_type="tree").inc(len(new_trees))
        generated_count += len(new_trees)

    if include_bushes:
        new_bushes = generate_bushes(scene, grid_spacing_m=bush_grid_spacing_m, min_bush_spacing_m=min_bush_spacing_m)
        scene.objects = [*scene.objects, *new_bushes]
        metrics.greenery_generated_total.labels(object_type="bush").inc(len(new_bushes))
        generated_count += len(new_bushes)

    if include_lawn:
        new_lawn = generate_lawn(scene, patch_size_m=lawn_patch_size_m)
        scene.objects = [*scene.objects, *new_lawn]
        metrics.greenery_generated_total.labels(object_type="lawn_patch").inc(len(new_lawn))
        generated_count += len(new_lawn)

    logging.getLogger("greencity.generate").info("сгенерировано озеленение: %d новых объектов", generated_count)
    return scene


# threading.Lock, не asyncio.Lock -- эти эндпоинты синхронные (def, не async
# def) и выполняются в пуле потоков FastAPI, вне event loop, где asyncio.Lock
# не работает предсказуемо между потоками. Замер этой же сессии: 3
# параллельных /api/greenplan/generate на крупном участке шли не параллельно,
# а ~11x медленнее КАЖДЫЙ (19.5с вместо 1.7с) -- чистая CPU-геометрия на
# shapely не распараллеливается общим GIL (см. тот же аргумент в
# backend/Dockerfile про UVICORN_WORKERS), конкурирующие потоки друг друга
# только тормозят. Лок сериализует запросы вместо того, чтобы позволить им
# толкаться за GIL -- сумма времени по факту меньше, а event loop (и
# healthcheck на нём) не голодает, пока несколько тяжёлых запросов crunch'ат
# геометрию одновременно.
_greenplan_generate_lock = threading.Lock()
_greenplan_report_lock = threading.Lock()


class GreenPlanGenerateResult(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment]
    violations: list[Violation]
    assortment: list[AssortmentRow]


# Синхронный def -- то же обоснование, что и у generate_greenery выше:
# deterministic_placement/violation_report/assortment_report -- чистая
# CPU-геометрия, ни одного await. НАМЕРЕННО без текста-отчёта (LLM) -- тот
# вынесен в отдельный /api/greenplan/report ниже: расстановка/нарушения/
# ведомость считаются за доли секунды (find_violations -- через STRtree,
# см. violation_report.py), а вызов Ollama занимает ~30 секунд сам по себе.
# Раньше оба шага были одним запросом, и фронтенд ждал уже готовый результат
# все эти 30 секунд ради текста, который к самой расстановке не относится.
@app.post("/api/greenplan/generate", response_model=GreenPlanGenerateResult)
def greenplan_generate(scene: Scene, k: int = Query(default=3, ge=1, le=9, description="Число ближайших проектов-соседей для retrieval.")):
    """Автоозеленение по прошлым проектам (GreenPlan, issue #23, Этапы 3-6) --
    в отличие от /api/generate-greenery (сетка без понимания похожих
    проектов), здесь паттерн для каждой геометрической зоны участка выбирается
    по тому, что реально делали архитекторы на похожих участках из
    retrieval-корпуса (backend/pattern_corpus.py), расстановка полностью
    детерминирована (backend/deterministic_placement.py).

    Каталог видов не параметризуется с фронта -- берутся все деревья/кусты
    базового каталога (backend/plant_catalog.py) по категории.

    Текст-объяснение -- отдельным запросом, см. /api/greenplan/report ниже:
    он занимает ~30 секунд (локальная LLM) и не должен блокировать уже
    готовый детерминированный результат.
    """
    with _greenplan_generate_lock:
        catalog = load_catalog()
        trees = [c for c in catalog if c.category == "tree"]
        bushes = [c for c in catalog if c.category == "bush" and c.object_type == "bush"]

        new_objects, assignments = generate_for_scene(scene, trees, bushes, k)
        scene.objects = [*scene.objects, *new_objects]

        logging.getLogger("greencity.greenplan").info(
            "GreenPlan: %d новых объектов, %d зон", len(new_objects), len(assignments)
        )

        return GreenPlanGenerateResult(
            scene=scene,
            assignments=assignments,
            violations=find_violations(scene),
            assortment=summarize_assortment(new_objects, catalog_by_id()),
        )


class GreenPlanReportResult(BaseModel):
    report: Optional[str]
    report_error: Optional[str]


# Синхронный def -- клиент openai блокирующий (как и у /api/edit-with-text
# ниже), FastAPI уводит в пул потоков; сам вызов -- ~30 секунд (mistral:7b
# локально), поэтому отдельный от /api/greenplan/generate эндпоинт: фронтенд
# показывает уже готовую расстановку сразу и дотягивает текст в фоне, не
# блокируя ничего остальным ожиданием LLM. Свой лок (не общий с generate
# выше) -- это разные ресурсы (CPU-геометрия vs сетевой вызов к Ollama),
# нет причины заставлять их ждать друг друга.
@app.post("/api/greenplan/report", response_model=GreenPlanReportResult)
def greenplan_report(assignments: list[ZoneAssignment]):
    """Текст-объяснение решений GreenPlan (Этап 6) через локальную LLM
    (mistral:7b/Ollama, backend/decision_report.py) -- по списку решений,
    уже посчитанному /api/greenplan/generate (передаётся сюда как есть, не
    пересчитывается). Недоступность Ollama -- не ошибка запроса: 200 с
    report=None и понятной report_error, а не 500."""
    with _greenplan_report_lock:
        try:
            report = generate_report(assignments)
            metrics.greenplan_generate_total.labels(report_outcome="success").inc()
            return GreenPlanReportResult(report=report, report_error=None)
        except DecisionReportUnavailable as e:
            metrics.greenplan_generate_total.labels(report_outcome="unavailable").inc()
            return GreenPlanReportResult(report=None, report_error=str(e))


# Синхронный def, а не async: клиент openai блокирующий, и FastAPI сам уводит
# такой обработчик в пул потоков -- ответ модели (~5-10 с) не подвесит
# остальные запросы.
@app.post("/api/edit-with-text", response_model=TextEditResult)
def edit_with_text(request: TextEditRequest):
    """Правка плана текстом (ТЗ: корректировки в текстовом формате).

    LLM возвращает не сцену, а список операций: групповых (ряд вдоль дорожек,
    группа у точки, удаление по условию) и точечных (add/remove/move/rotate).
    Координаты считает и проверяет по нормам планировщик (llm_editor.apply_plan,
    placement.py). В ответе -- новая сцена и что применено, что отклонено и
    почему, какие есть предупреждения.
    """
    try:
        result = edit_scene_with_text(request.scene, request.instruction)
    except LlmNotConfiguredError as e:
        metrics.llm_edit_requests_total.labels(outcome="not_configured").inc()
        raise HTTPException(503, str(e)) from e
    except LlmError as e:
        metrics.llm_edit_requests_total.labels(outcome="llm_error").inc()
        raise HTTPException(502, str(e)) from e
    metrics.llm_edit_requests_total.labels(outcome="success").inc()
    return result


# Синхронный def -- ezdxf.write -- обычная блокирующая сборка текста в
# памяти, ни одного await, как и у /api/parse/generate-greenery выше.
@app.post("/api/export-dxf")
def export_dxf_endpoint(scene: Scene):
    """Итоговый план -> файл .dxf для скачивания (ТЗ: "итоговый план должен
    экспортироваться обратно в формат DXF"). Тело запроса -- та же Scene, что
    фронтенд и так держит в состоянии редактора (после парсинга/генерации/
    правок текстом/вручную) -- ничего дополнительно спрашивать не нужно.

    Слои и геометрия -- зеркало parser/parse_dxf.py, см. докстринг
    export_dxf.py: файл открывается в любом CAD и, если нужно, читается
    обратно тем же parse_dxf.py.
    """
    doc = scene_to_dxf(scene)
    buf = io.StringIO()
    doc.write(buf)
    metrics.dxf_exports_total.inc()
    logging.getLogger("greencity.export").info("экспортирована сцена: %d объектов", len(scene.objects))
    return Response(
        content=buf.getvalue(),
        media_type="application/dxf",
        headers={"Content-Disposition": 'attachment; filename="greencity_plan.dxf"'},
    )


# --- Аккаунты и проекты (auth.py, projects.py, db.py, cache.py) --------------
#
# Регистрация -- только логин/пароль, без подтверждения почты (по условию
# задачи). Гостевой режим -- это отсутствие этих эндпоинтов в обиходе:
# распарсить DXF, сгенерировать растительность и править текстом можно и без
# токена (эндпоинты выше его не требуют) -- только сохранить именованный
# проект нельзя, для этого и нужен аккаунт.
#
# Всё здесь асинхронно (AsyncMongoClient в db.py, redis.asyncio в cache.py) --
# сервис рассчитан на много одновременных пользователей, блокирующие клиенты
# заняли бы поток из ограниченного пула на каждое обращение к базе.
#
# Ошибки бизнес-логики (занятое имя, неверный пароль, лимит проектов, чужой
# проект, недействительный refresh-токен) -- ожидаемые, превращаются в
# понятный 400/401/404, а не 500; недоступность самой MongoDB -- в 503, как и
# недоступность LLM выше.


_auth_logger = logging.getLogger("greencity.auth")


def _mongo_unavailable(e: PyMongoError) -> HTTPException:
    logging.getLogger("greencity.db").warning("MongoDB недоступна на запросе: %s", e)
    return HTTPException(503, "Хранилище аккаунтов и проектов сейчас недоступно, попробуйте позже")


# Ограничение частоты (cache.py, Redis) -- по IP, отдельно на регистрацию и
# на вход: при большом числе пользователей это единственная защита от
# подбора пароля/массовой регистрации ботами, которая тут вообще есть, раз
# самого подтверждения почты по условию задачи нет. Недоступный Redis не
# блокирует вход -- см. cache.check_rate_limit.
REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S = 5, 3600  # 5 регистраций в час с одного адреса
LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S = 10, 300  # 10 попыток входа за 5 минут с одного адреса


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/api/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, http_request: Request):
    """Регистрация: только имя пользователя и пароль, без почты и её
    подтверждения. Сразу возвращает access- и refresh-токен -- отдельный
    вход после регистрации не нужен."""
    client_ip = _client_ip(http_request)
    if not await cache.check_rate_limit(f"rl:register:{client_ip}", REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_S):
        metrics.auth_registrations_total.labels(outcome="rate_limited").inc()
        metrics.rate_limit_blocks_total.labels(endpoint="register").inc()
        _auth_logger.warning("регистрация отклонена лимитом частоты", extra={"client_ip": client_ip})
        raise HTTPException(429, "Слишком много регистраций с этого адреса, попробуйте позже")
    try:
        result = await projects_service.register(request.username, request.password)
    except ProjectsError as e:
        metrics.auth_registrations_total.labels(outcome="rejected").inc()
        _auth_logger.info("регистрация отклонена: %s", e, extra={"client_ip": client_ip, "username": request.username})
        raise HTTPException(400, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    metrics.auth_registrations_total.labels(outcome="success").inc()
    _auth_logger.info("зарегистрирован новый пользователь", extra={"client_ip": client_ip, "username": request.username})
    return result


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, http_request: Request):
    client_ip = _client_ip(http_request)
    rate_key = f"rl:login:{client_ip}"
    if not await cache.check_rate_limit(rate_key, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_S):
        metrics.auth_logins_total.labels(outcome="rate_limited").inc()
        metrics.rate_limit_blocks_total.labels(endpoint="login").inc()
        _auth_logger.warning("вход отклонён лимитом частоты", extra={"client_ip": client_ip})
        raise HTTPException(429, "Слишком много попыток входа с этого адреса, попробуйте позже")
    try:
        result = await projects_service.login(request.username, request.password)
    except ProjectsError as e:
        metrics.auth_logins_total.labels(outcome="rejected").inc()
        _auth_logger.info("вход отклонён: %s", e, extra={"client_ip": client_ip, "username": request.username})
        raise HTTPException(401, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    await cache.reset_rate_limit(rate_key)  # успешный вход -- не копить неудачные попытки на будущее
    metrics.auth_logins_total.labels(outcome="success").inc()
    _auth_logger.info("успешный вход", extra={"client_ip": client_ip, "username": request.username})
    return result


@app.post("/api/auth/refresh", response_model=AccessTokenResponse)
async def refresh(request: RefreshRequest):
    """Новый access-токен по refresh-токену -- фронтенд дёргает это сам,
    когда прежний access-токен истёк (см. frontend/src/auth.ts), не спрашивая
    пароль заново. Отозванный (logout) или битый refresh-токен -- 401."""
    try:
        access_token, username = await refresh_access_token(request.refresh_token)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return AccessTokenResponse(access_token=access_token, username=username)


@app.post("/api/auth/logout")
async def logout(request: RefreshRequest):
    """Отозвать refresh-токен немедленно (Redis: cache.revoke_session), не
    дожидаясь истечения REFRESH_TOKEN_TTL_SECONDS -- без этого выход был бы
    только локальным удалением токенов во фронтенде, а сам refresh-токен
    оставался бы действителен ещё до 30 дней. Не требует access-токена --
    предъявление самого refresh-токена и есть право его отозвать, как и в
    типичных эндпоинтах отзыва OAuth."""
    payload = decode_token(request.refresh_token)
    sid = payload.get("sid") if payload else None
    if sid:
        await cache.revoke_session(sid)
        _auth_logger.info("выход, сессия отозвана", extra={"username": payload.get("username")})
    return {"status": "ok"}


@app.get("/api/auth/me", response_model=MeResponse)
async def whoami(user: CurrentUser = Depends(require_user)):
    """Проверить access-токен и узнать, под кем он выдан -- фронтенд дёргает
    это при загрузке страницы, чтобы решить, показывать вход или уже
    авторизованный вид."""
    return MeResponse(username=user.username)


@app.get("/api/projects", response_model=list[ProjectSummary])
async def list_projects(user: CurrentUser = Depends(require_user)):
    try:
        return await projects_service.list_projects(user.id)
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@app.post("/api/projects", response_model=Project)
async def create_project(request: ProjectCreate, user: CurrentUser = Depends(require_user)):
    """Сохранить текущую сцену как новый проект. Не больше
    projects.MAX_PROJECTS_PER_USER на пользователя (по условию задачи) --
    лишний создать нельзя, нужно сперва удалить один из существующих."""
    try:
        return await projects_service.create_project(user.id, request.name, request.scene)
    except ProjectsError as e:
        raise HTTPException(400, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        return await projects_service.get_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@app.put("/api/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, request: ProjectUpdate, user: CurrentUser = Depends(require_user)):
    """Сохранить правки в уже существующий проект -- переименовать,
    перезаписать сцену, или и то, и другое; оба поля необязательны."""
    try:
        return await projects_service.update_project(user.id, project_id, request.name, request.scene)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, user: CurrentUser = Depends(require_user)):
    try:
        await projects_service.delete_project(user.id, project_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except PyMongoError as e:
        raise _mongo_unavailable(e) from e
    return {"status": "ok"}
