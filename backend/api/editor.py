"""
Эндпоинты ручного редактора: разбор DXF и папки DWG, каталог видов и МАФ,
генерация озеленения по сетке, правка плана текстом через LLM, экспорт в DXF.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import TypeAdapter

from core.building_setbacks import compute_building_setbacks
from core.plant_catalog import CatalogItem, load_catalog
from core.schemas import Scene
from core.setback_norms import DEFAULT_TREE_SPECIES
from exchange import dwg_job
from exchange.dxf_parser import parse_dxf_file
from exchange.export_dxf import scene_to_dxf
from generation.greenery_generator import (
    DEFAULT_BUSH_GRID_SPACING_M,
    DEFAULT_GRID_SPACING_M,
    DEFAULT_LAWN_PATCH_SIZE_M,
    DEFAULT_MIN_BUSH_SPACING_M,
    DEFAULT_MIN_TREE_SPACING_M,
    MAX_ALLOWED_GRID_SPACING_M,
    MAX_ALLOWED_LAWN_PATCH_SIZE_M,
    MIN_ALLOWED_GRID_SPACING_M,
    MIN_ALLOWED_LAWN_PATCH_SIZE_M,
    generate_bushes,
    generate_lawn,
    generate_trees,
)
from monitoring import metrics
from monitoring.logging_config import current_request_id
from storage import cache
from text_editor.service import (
    LlmError,
    LlmNotConfiguredError,
    TextEditRequest,
    TextEditResult,
    edit_scene_with_text,
)

router = APIRouter()


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


@router.get("/api/catalog", response_model=list[CatalogItem])
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
@router.post("/api/parse")
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


# Синхронный def по той же причине, что и /api/parse выше -- субпроцессы
# dwg2dxf и геометрия ezdxf/shapely блокирующие, await тут нечему быть.
# Частичный успех -- штатный сценарий (issue #50): реальные DWG-проекты
# приходят россыпью в 20-30 файлов, часть из которых либо почти пустая
# сборка с нерезолвленными xref-ссылками, либо содержит объекты, которые
# LibreDWG не умеет читать (см. docstring dwg_batch_converter.py) -- поэтому
# при хотя бы одном успешно сконвертированном файле возвращаем 200 с
# результатом и списком неудач в dwgConversionWarnings, а не роняем весь
# запрос.
@router.post("/api/parse-dwg")
def parse_dwg_folder_endpoint(files: list[UploadFile] = File(...)):
    dwg_files = [f for f in files if f.filename.lower().endswith(".dwg")]
    if not dwg_files:
        raise HTTPException(400, "Среди загруженных файлов нет ни одного .dwg")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        dwg_paths = []
        for f in dwg_files:
            # Path(f.filename).name -- обрезает любые каталоговые компоненты
            # (в т.ч. "../"), которые клиент может подсунуть в имени файла
            # multipart-запроса, прежде чем строить путь внутри tmp_path.
            dest = tmp_path / Path(f.filename).name
            dest.write_bytes(f.file.read())
            dwg_paths.append(dest)

        # Сам разбор -- в отдельном процессе и не больше DWG_MAX_PARALLEL
        # пачек одновременно на весь backend: пачка держит до ~1,8 ГБ, и
        # одновременные загрузки прямо в процессах uvicorn убивали их по
        # памяти (502). См. docstring exchange/dwg_job.py.
        try:
            with dwg_job.slot():
                payload, summary = dwg_job.run(dwg_paths, tmp_path, request_id=current_request_id())
        except dwg_job.DwgJobError as e:
            metrics.dwg_batch_conversions_total.labels(outcome=e.outcome).inc()
            if e.failed:
                metrics.dwg_files_processed_total.labels(outcome="failed").inc(e.failed)
            if e.outcome == "parse_error":
                metrics.dxf_parse_errors_total.inc()
            raise HTTPException(e.status, e.detail) from e

    metrics.dwg_batch_conversions_total.labels(outcome="success").inc()
    metrics.dwg_files_processed_total.labels(outcome="converted").inc(summary["converted"])
    metrics.dwg_files_processed_total.labels(outcome="failed").inc(summary["failed"])
    metrics.dxf_parses_total.inc()
    logging.getLogger("greencity.parse").info(
        "DWG-батч разобран: %d/%d файлов сконвертировано, %d объектов, %d зон",
        summary["converted"],
        len(dwg_files),
        summary["objects"],
        summary["zones"],
    )

    # Готовый JSON из дочернего процесса -- как есть, без разбора в словарь.
    return Response(content=payload, media_type="application/json")


# Тоже синхронный def -- по той же причине, что и /api/parse выше: внутри
# только блокирующая геометрия (shapely), ни одного await. Раньше сигнатура
# была async def без единого await внутри -- классический анти-паттерн,
# который на каждый вызов подвешивал event loop на всё время генерации.
@router.post("/api/generate-greenery", response_model=Optional[Scene])
def generate_greenery(
    scene: Scene,
    species: Optional[str] = Query(
        default=None,
        description=(
            "Вид дерева (например 'Липа мелколистная'). Влияет на отступы "
            "от ограничений, если для его рода есть правило в "
            "core.setback_norms.SPECIES_SETBACK_RULES (липа/клён/дуб -- 10 м от "
            "здания по 743-ПП и т.п.); для остальных видов -- общая норма "
            "для дерева. Не задан -- смесь видов, отступ по самому строгому "
            f"из них (основной вид -- {DEFAULT_TREE_SPECIES!r})."
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


# Синхронный def, а не async: клиент openai блокирующий, и FastAPI сам уводит
# такой обработчик в пул потоков -- ответ модели (~5-10 с) не подвесит
# остальные запросы.
@router.post("/api/edit-with-text", response_model=TextEditResult)
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
@router.post("/api/export-dxf")
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
