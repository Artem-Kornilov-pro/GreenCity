"""
Эндпоинты редактора: загрузка DXF и папки DWG, каталог видов и малых форм,
генерация по сетке, правка плана текстом, экспорт в DXF.

Все обработчики асинхронные. Тяжёлая синхронная работа (ezdxf, shapely)
идёт в пуле потоков с ограничением очереди (core/concurrency.py), разбор и
экспорт DWG -- в отдельном процессе, запрос к LLM -- асинхронным клиентом.
Тяжёлые эндпоинты защищены лимитами частоты и размера (api/protection.py).
"""

from __future__ import annotations

import io
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import ezdxf
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import TypeAdapter

from api.protection import HEAVY_RESPONSES, MAX_DWG_FILES, rate_limit
from core.building_setbacks import compute_building_setbacks
from core.concurrency import ServerBusy, run_heavy, run_light
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
    catalog = await run_light(load_catalog)
    await cache.set_cached(CATALOG_CACHE_KEY, _CATALOG_ADAPTER.dump_json(catalog).decode("utf-8"), CATALOG_CACHE_TTL_S)
    return catalog


def _parse_dxf(data: bytes, filename: str) -> dict:
    # delete=False: на Windows открытый временный файл нельзя открыть повторно по имени.
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)  # noqa: SIM115
    try:
        tmp.write(data)
        tmp.close()
        try:
            scene = parse_dxf_file(tmp.name)
        except Exception as e:
            metrics.dxf_parse_errors_total.inc()
            log.warning("не удалось разобрать %r: %s", filename, e)
            raise HTTPException(400, f"Не удалось разобрать DXF: {e}") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    scene["buildingSetbacks"] = compute_building_setbacks(scene.get("objects", []))
    source_id = source_store.save_dxf(data)
    if source_id:
        scene["meta"]["sourceId"] = source_id
    return scene


@router.post(
    "/api/parse",
    tags=["Чертежи"],
    summary="Загрузить чертёж DXF",
    responses={**SCENE_RESPONSE, **HEAVY_RESPONSES},
    dependencies=[rate_limit("parse")],
)
async def parse_dxf_endpoint(file: UploadFile = File(..., description="Чертёж участка в формате DXF.")):
    """Разбирает DXF по слоям: граница участка, здания, дороги, дорожки,
    подземные сети и охранные зоны, существующие посадки и малые формы.
    Координаты сцены -- метры от центра участка. Исходный файл сохраняется
    на сервере (`meta.sourceId`): экспорт допишет результат поверх него."""
    if not (file.filename or "").lower().endswith(".dxf"):
        raise HTTPException(400, "Ожидается файл .dxf")

    data = await file.read()
    scene = await run_heavy(_parse_dxf, data, file.filename)
    metrics.dxf_parses_total.inc()
    log.info("разобран %r: %d объектов, %d зон", file.filename, len(scene.get("objects", [])), len(scene.get("restrictions", [])))
    return scene


@router.post(
    "/api/parse-dwg",
    tags=["Чертежи"],
    summary="Загрузить папку DWG",
    responses={**SCENE_RESPONSE, **HEAVY_RESPONSES},
    dependencies=[rate_limit("parse_dwg")],
)
async def parse_dwg_folder_endpoint(
    files: list[UploadFile] = File(..., description="Файлы .dwg проекта; остальные файлы папки игнорируются."),
):
    """Конвертирует каждый DWG в DXF (LibreDWG), сливает в один чертёж и
    разбирает так же, как `/api/parse`. Файлы, которые не удалось прочитать,
    не прерывают загрузку: они перечислены в `dwgConversionWarnings`.
    Разбор идёт в отдельном процессе; одновременно -- не больше
    `DWG_MAX_PARALLEL` пачек, остальные ждут очереди."""
    dwg_files = [f for f in files if (f.filename or "").lower().endswith(".dwg")]
    if not dwg_files:
        raise HTTPException(400, "Среди загруженных файлов нет ни одного .dwg")
    if len(dwg_files) > MAX_DWG_FILES:
        raise HTTPException(400, f"Не больше {MAX_DWG_FILES} файлов .dwg за одну загрузку")

    tmp_path = Path(await run_light(tempfile.mkdtemp))
    try:
        dwg_paths = []
        for f in dwg_files:
            dest = tmp_path / Path(f.filename).name  # .name отрезает "../" из имени файла
            await run_light(dest.write_bytes, await f.read())
            dwg_paths.append(dest)

        source_id = await run_light(source_store.save_dwg_batch, dwg_paths)
        try:
            async with dwg_job.slot():
                payload, summary = await dwg_job.run(dwg_paths, tmp_path, request_id=current_request_id(), source_id=source_id)
        except dwg_job.DwgJobError as e:
            metrics.dwg_batch_conversions_total.labels(outcome=e.outcome).inc()
            if e.failed:
                metrics.dwg_files_processed_total.labels(outcome="failed").inc(e.failed)
            if e.outcome == "parse_error":
                metrics.dxf_parse_errors_total.inc()
            raise HTTPException(e.status, e.detail) from e
    finally:
        await run_light(shutil.rmtree, tmp_path, True)

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
    responses=HEAVY_RESPONSES,
    dependencies=[rate_limit("generate")],
)
async def generate_greenery(
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
    return await run_heavy(
        _generate_greenery,
        scene,
        species=species,
        grid_spacing_m=grid_spacing_m,
        min_tree_spacing_m=min_tree_spacing_m,
        include_trees=include_trees,
        include_bushes=include_bushes,
        bush_grid_spacing_m=bush_grid_spacing_m,
        min_bush_spacing_m=min_bush_spacing_m,
        include_lawn=include_lawn,
        lawn_patch_size_m=lawn_patch_size_m,
    )


def _generate_greenery(
    scene: Scene,
    *,
    species: Optional[str],
    grid_spacing_m: Optional[float],
    min_tree_spacing_m: Optional[float],
    include_trees: bool,
    include_bushes: bool,
    bush_grid_spacing_m: Optional[float],
    min_bush_spacing_m: Optional[float],
    include_lawn: bool,
    lawn_patch_size_m: Optional[float],
) -> Scene:
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
    responses={
        **HEAVY_RESPONSES,
        502: {"description": "Модель недоступна или ответила неразборчиво."},
        503: {"description": "Ключ LLM не настроен или сервер занят расчётами."},
    },
    dependencies=[rate_limit("llm")],
)
async def edit_with_text(request: TextEditRequest):
    """Просьба на русском («посади липы вдоль дорожек», «убери лавки у
    парковки») превращается моделью в операции, а координаты и проверку норм
    выполняет планировщик. В ответе -- новая сцена и что применено,
    отклонено и почему. Если модель выбрала озеленение всего участка, в поле
    `greenplan` -- параметры для `/api/greenplan/generate`."""
    try:
        result = await edit_scene_with_text(request.scene, request.instruction, request.history)
    except LlmNotConfiguredError as e:
        metrics.llm_edit_requests_total.labels(outcome="not_configured").inc()
        raise HTTPException(503, str(e)) from e
    except LlmError as e:
        metrics.llm_edit_requests_total.labels(outcome="llm_error").inc()
        raise HTTPException(502, str(e)) from e
    metrics.llm_edit_requests_total.labels(outcome="success").inc()
    return result


def _overlay_dxf(path: Path, scene: Scene) -> bytes:
    doc = ezdxf.readfile(path)
    summary = overlay_scene(scene, doc)
    buf = io.StringIO()
    doc.write(buf)
    logging.getLogger("greencity.export").info("экспорт поверх исходного DXF: %s", summary.layers)
    return buf.getvalue().encode("utf-8")


def _rebuilt_dxf(scene: Scene) -> bytes:
    buf = io.StringIO()
    scene_to_dxf(scene).write(buf)
    return buf.getvalue().encode("utf-8")


async def _overlay_dwg(paths: list[Path], scene: Scene) -> bytes:
    tmp_path = Path(await run_light(tempfile.mkdtemp))
    try:
        async with dwg_job.slot():
            return await dwg_job.run_export(paths, tmp_path, scene, request_id=current_request_id())
    finally:
        await run_light(shutil.rmtree, tmp_path, True)


@router.post(
    "/api/export-dxf",
    tags=["Чертежи"],
    summary="Экспорт плана в DXF",
    responses={**DXF_RESPONSE, **HEAVY_RESPONSES},
    dependencies=[rate_limit("export")],
)
async def export_dxf_endpoint(scene: Scene):
    """Исходный чертёж без изменений плюс слои результата в его координатах:
    `NEW_*` -- посадка и благоустройство GreenPlan, `USER_*` -- правки
    пользователя, `USER_REMOVED` -- места удалённых и перенесённых исходных
    объектов. У каждой сущности результата XDATA с id посадки -- тем же, что
    в файле объяснений. Если исходника на сервере нет, DXF собирается из
    сцены (метры от центра участка)."""
    export_log = logging.getLogger("greencity.export")
    mode = "rebuilt"
    content: Optional[bytes] = None
    found = await run_light(source_store.find, scene.meta.sourceId)
    try:
        if found and found[0] == "dxf":
            content, mode = await run_heavy(_overlay_dxf, found[1], scene), "overlay"
        elif found and found[0] == "dwg":
            content, mode = await _overlay_dwg(found[1], scene), "overlay"
            export_log.info("экспорт поверх исходной пачки DWG (%d файлов)", len(found[1]))
    except ServerBusy:
        raise
    except Exception as e:  # noqa: BLE001 -- при любом сбое наложения отдаём сборку из сцены, а не 500
        export_log.warning("экспорт поверх исходника не удался, DXF собран из сцены: %s", e)
        content, mode = None, "rebuilt"
    if content is None:
        content = await run_heavy(_rebuilt_dxf, scene)
    metrics.dxf_exports_total.inc()
    export_log.info("экспортирована сцена (%s): %d объектов", mode, len(scene.objects))
    return Response(
        content=content,
        media_type="application/dxf",
        headers={"Content-Disposition": 'attachment; filename="greencity_plan.dxf"', "X-GreenCity-Export": mode},
    )
