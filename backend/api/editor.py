"""
Эндпоинты редактора: загрузка DXF и папки DWG, каталог видов и малых форм,
генерация по сетке, правка плана текстом, экспорт в DXF.

Обработчики с тяжёлой синхронной работой (ezdxf, shapely, dwg2dxf, клиент
LLM) объявлены обычным def: FastAPI выполняет их в пуле потоков и не
блокирует event loop.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Optional

import ezdxf
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import TypeAdapter

from core.building_setbacks import compute_building_setbacks
from core.plant_catalog import CatalogItem, load_catalog
from core.schemas import Scene
from core.setback_norms import DEFAULT_TREE_SPECIES
from exchange import dwg_job, source_store
from exchange.dxf_overlay import overlay_scene
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
log = logging.getLogger("greencity.parse")

_CATALOG_ADAPTER = TypeAdapter(list[CatalogItem])
CATALOG_CACHE_KEY = "catalog:v1"
# Коротко: новый пак моделей должен становиться виден без перезапуска.
CATALOG_CACHE_TTL_S = 30

SCENE_RESPONSE = {200: {"model": Scene, "description": "Сцена участка: граница, зоны ограничений, объекты."}}
DXF_RESPONSE = {
    200: {
        "content": {"application/dxf": {}},
        "description": "Файл DXF. Заголовок X-GreenCity-Export: overlay (поверх исходного чертежа) или rebuilt (собран из сцены).",
    }
}


@router.get(
    "/api/catalog",
    response_model=list[CatalogItem],
    tags=["Редактор"],
    summary="Каталог видов и малых форм",
)
async def get_catalog():
    """Виды растений из ассортимента Москвы, покрытия и малые архитектурные
    формы с размерами, классом формы и 3D-моделью."""
    cached = await cache.get_cached(CATALOG_CACHE_KEY)
    if cached is not None:
        return _CATALOG_ADAPTER.validate_json(cached)
    catalog = load_catalog()
    await cache.set_cached(CATALOG_CACHE_KEY, _CATALOG_ADAPTER.dump_json(catalog).decode("utf-8"), CATALOG_CACHE_TTL_S)
    return catalog


@router.post("/api/parse", tags=["Чертежи"], summary="Загрузить чертёж DXF", responses=SCENE_RESPONSE)
def parse_dxf_endpoint(file: UploadFile = File(..., description="Чертёж участка в формате DXF.")):
    """Разбирает DXF по слоям: граница участка, здания, дороги, дорожки,
    подземные сети и охранные зоны, существующие посадки и малые формы.
    Координаты сцены -- метры от центра участка. Исходный файл сохраняется
    на сервере (`meta.sourceId`): экспорт допишет результат поверх него."""
    if not file.filename.lower().endswith(".dxf"):
        raise HTTPException(400, "Ожидается файл .dxf")

    data = file.file.read()
    # delete=False: на Windows открытый временный файл нельзя открыть повторно по имени.
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)  # noqa: SIM115
    try:
        tmp.write(data)
        tmp.close()
        try:
            scene = parse_dxf_file(tmp.name)
        except Exception as e:
            metrics.dxf_parse_errors_total.inc()
            log.warning("не удалось разобрать %r: %s", file.filename, e)
            raise HTTPException(400, f"Не удалось разобрать DXF: {e}") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    scene["buildingSetbacks"] = compute_building_setbacks(scene.get("objects", []))
    source_id = source_store.save_dxf(data)
    if source_id:
        scene["meta"]["sourceId"] = source_id

    metrics.dxf_parses_total.inc()
    log.info("разобран %r: %d объектов, %d зон", file.filename, len(scene.get("objects", [])), len(scene.get("restrictions", [])))
    return scene


@router.post("/api/parse-dwg", tags=["Чертежи"], summary="Загрузить папку DWG", responses=SCENE_RESPONSE)
def parse_dwg_folder_endpoint(files: list[UploadFile] = File(..., description="Файлы .dwg проекта; остальные файлы папки игнорируются.")):
    """Конвертирует каждый DWG в DXF (LibreDWG), сливает в один чертёж и
    разбирает так же, как `/api/parse`. Файлы, которые не удалось прочитать,
    не прерывают загрузку: они перечислены в `dwgConversionWarnings`.
    Разбор идёт в отдельном процессе; одновременно -- не больше
    `DWG_MAX_PARALLEL` пачек, остальные ждут очереди."""
    dwg_files = [f for f in files if f.filename.lower().endswith(".dwg")]
    if not dwg_files:
        raise HTTPException(400, "Среди загруженных файлов нет ни одного .dwg")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        dwg_paths = []
        for f in dwg_files:
            dest = tmp_path / Path(f.filename).name  # .name отрезает "../" из имени файла
            dest.write_bytes(f.file.read())
            dwg_paths.append(dest)

        source_id = source_store.save_dwg_batch(dwg_paths)
        try:
            with dwg_job.slot():
                payload, summary = dwg_job.run(dwg_paths, tmp_path, request_id=current_request_id(), source_id=source_id)
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
    log.info(
        "DWG-пачка разобрана: %d/%d файлов, %d объектов, %d зон",
        summary["converted"], len(dwg_files), summary["objects"], summary["zones"],
    )
    # JSON из дочернего процесса отдаётся как есть, без повторного разбора.
    return Response(content=payload, media_type="application/json")


@router.post(
    "/api/generate-greenery",
    response_model=Optional[Scene],
    tags=["Редактор"],
    summary="Простая генерация по сетке",
)
def generate_greenery(
    scene: Scene,
    species: Optional[str] = Query(
        default=None,
        description=(
            "Вид дерева, например «Липа мелколистная». Учитывает правила по породе "
            f"(липа, клён, дуб — 10 м от здания). Не задан — смесь видов, основной — {DEFAULT_TREE_SPECIES!r}."
        ),
    ),
    grid_spacing_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_GRID_SPACING_M,
        le=MAX_ALLOWED_GRID_SPACING_M,
        description=f"Шаг сетки точек посадки деревьев, м. По умолчанию {DEFAULT_GRID_SPACING_M}.",
    ),
    min_tree_spacing_m: Optional[float] = Query(
        default=None, ge=0.0, description=f"Минимальное расстояние между деревьями, м. По умолчанию {DEFAULT_MIN_TREE_SPACING_M}."
    ),
    include_trees: bool = Query(default=True, description="Сажать деревья."),
    include_bushes: bool = Query(default=True, description="Сажать группы кустарников."),
    bush_grid_spacing_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_GRID_SPACING_M,
        le=MAX_ALLOWED_GRID_SPACING_M,
        description=f"Шаг сетки центров групп кустарников, м. По умолчанию {DEFAULT_BUSH_GRID_SPACING_M}.",
    ),
    min_bush_spacing_m: Optional[float] = Query(
        default=None, ge=0.0, description=f"Минимальное расстояние между центрами групп кустарников, м. По умолчанию {DEFAULT_MIN_BUSH_SPACING_M}."
    ),
    include_lawn: bool = Query(default=True, description="Заполнить свободную площадь газоном."),
    lawn_patch_size_m: Optional[float] = Query(
        default=None,
        ge=MIN_ALLOWED_LAWN_PATCH_SIZE_M,
        le=MAX_ALLOWED_LAWN_PATCH_SIZE_M,
        description=f"Сторона плитки газона, м. По умолчанию {DEFAULT_LAWN_PATCH_SIZE_M}.",
    ),
):
    """Упрощённая генерация без аналогов: деревья по сетке, группы
    кустарников и газон в разрешённых зонах с соблюдением отступов.
    Основной способ озеленения -- `/api/greenplan/generate`."""
    generated = 0
    if include_trees:
        new_trees = generate_trees(scene, species=species, grid_spacing_m=grid_spacing_m, min_tree_spacing_m=min_tree_spacing_m)
        scene.objects = [*scene.objects, *new_trees]
        metrics.greenery_generated_total.labels(object_type="tree").inc(len(new_trees))
        generated += len(new_trees)
    if include_bushes:
        new_bushes = generate_bushes(scene, grid_spacing_m=bush_grid_spacing_m, min_bush_spacing_m=min_bush_spacing_m)
        scene.objects = [*scene.objects, *new_bushes]
        metrics.greenery_generated_total.labels(object_type="bush").inc(len(new_bushes))
        generated += len(new_bushes)
    if include_lawn:
        new_lawn = generate_lawn(scene, patch_size_m=lawn_patch_size_m)
        scene.objects = [*scene.objects, *new_lawn]
        metrics.greenery_generated_total.labels(object_type="lawn_patch").inc(len(new_lawn))
        generated += len(new_lawn)
    logging.getLogger("greencity.generate").info("сгенерировано по сетке: %d объектов", generated)
    return scene


@router.post(
    "/api/edit-with-text",
    response_model=TextEditResult,
    tags=["Редактор"],
    summary="Правка плана текстом (ИИ-ассистент)",
    responses={502: {"description": "Модель недоступна или ответила неразборчиво."}, 503: {"description": "Ключ LLM не настроен."}},
)
def edit_with_text(request: TextEditRequest):
    """Просьба на русском («посади липы вдоль дорожек», «убери лавки у
    парковки») превращается моделью в операции, а координаты и проверку норм
    выполняет планировщик. В ответе -- новая сцена и что применено,
    отклонено и почему. Если модель выбрала озеленение всего участка, в поле
    `greenplan` -- параметры для `/api/greenplan/generate`."""
    try:
        result = edit_scene_with_text(request.scene, request.instruction, request.history)
    except LlmNotConfiguredError as e:
        metrics.llm_edit_requests_total.labels(outcome="not_configured").inc()
        raise HTTPException(503, str(e)) from e
    except LlmError as e:
        metrics.llm_edit_requests_total.labels(outcome="llm_error").inc()
        raise HTTPException(502, str(e)) from e
    metrics.llm_edit_requests_total.labels(outcome="success").inc()
    return result


@router.post("/api/export-dxf", tags=["Чертежи"], summary="Экспорт плана в DXF", responses=DXF_RESPONSE)
def export_dxf_endpoint(scene: Scene):
    """Исходный чертёж без изменений плюс слои результата в его координатах:
    `NEW_*` -- посадка и благоустройство GreenPlan, `USER_*` -- правки
    пользователя, `USER_REMOVED` -- места удалённых и перенесённых исходных
    объектов. У каждой сущности результата XDATA с id посадки -- тем же, что
    в файле объяснений. Если исходника на сервере нет, DXF собирается из
    сцены (метры от центра участка)."""
    export_log = logging.getLogger("greencity.export")
    mode = "rebuilt"
    content: Optional[bytes] = None
    found = source_store.find(scene.meta.sourceId)
    try:
        if found and found[0] == "dxf":
            doc = ezdxf.readfile(found[1])
            summary = overlay_scene(scene, doc)
            buf = io.StringIO()
            doc.write(buf)
            content, mode = buf.getvalue().encode("utf-8"), "overlay"
            export_log.info("экспорт поверх исходного DXF: %s", summary.layers)
        elif found and found[0] == "dwg":
            with tempfile.TemporaryDirectory() as tmp_dir, dwg_job.slot():
                content = dwg_job.run_export(found[1], Path(tmp_dir), scene, request_id=current_request_id())
            mode = "overlay"
            export_log.info("экспорт поверх исходной пачки DWG (%d файлов)", len(found[1]))
    except Exception as e:  # noqa: BLE001 -- при любом сбое наложения отдаём сборку из сцены, а не 500
        export_log.warning("экспорт поверх исходника не удался, DXF собран из сцены: %s", e)
        content, mode = None, "rebuilt"
    if content is None:
        buf = io.StringIO()
        scene_to_dxf(scene).write(buf)
        content = buf.getvalue().encode("utf-8")
    metrics.dxf_exports_total.inc()
    export_log.info("экспортирована сцена (%s): %d объектов", mode, len(scene.objects))
    return Response(
        content=content,
        media_type="application/dxf",
        headers={
            "Content-Disposition": 'attachment; filename="greencity_plan.dxf"',
            "X-GreenCity-Export": mode,
            "Access-Control-Expose-Headers": "X-GreenCity-Export",
        },
    )
