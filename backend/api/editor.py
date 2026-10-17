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
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, TypeAdapter

from api.protection import HEAVY_RESPONSES, MAX_DWG_FILES, rate_limit
from core.concurrency import ServerBusy, run_heavy, run_light
from core.plant_catalog import CatalogItem, load_catalog
from core.schemas import Scene
from core.setback_norms import DEFAULT_TREE_SPECIES
from core.versions import sha256_of, sha256_of_files
from exchange import drawing_package, dwg_batch_converter, dwg_job, source_store
from exchange.dxf_overlay import overlay_scene, stamp_versions
from exchange.dxf_parser import LAYER_KINDS, complete_scene, parse_dxf_file, read_dxf
from exchange.export_check import ExportVerification, verify_export
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


def _parse_dxf(data: bytes, filename: str, layer_map: Optional[dict[str, str]] = None) -> dict:
    # delete=False: на Windows открытый временный файл нельзя открыть повторно по имени.
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)  # noqa: SIM115
    try:
        tmp.write(data)
        tmp.close()
        try:
            scene = parse_dxf_file(tmp.name, layer_map=layer_map) if layer_map else parse_dxf_file(tmp.name)
        except Exception as e:
            metrics.dxf_parse_errors_total.inc()
            log.warning("не удалось разобрать %r: %s", filename, e)
            raise HTTPException(400, f"Не удалось разобрать DXF: {e}") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    complete_scene(scene)
    scene["meta"]["sourceSha256"] = sha256_of(data)
    source_id = source_store.save_dxf(data)
    if source_id:
        scene["meta"]["sourceId"] = source_id
    return scene


LAYER_MAP_DESCRIPTION = (
    "Назначения слоёв, главнее автоматического распознавания: JSON {имя слоя: класс}, "
    "классы -- GET /api/layer-kinds. Например {\"Слой 5\": \"building\", \"0\": \"ignore\"}."
)


def _layer_map(raw: Optional[str]) -> Optional[dict[str, str]]:
    """Назначения слоёв из поля формы; неизвестный класс -- 400, а не тихий
    пропуск: пользователь должен узнать, что его назначение не применилось."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"layerMap: ожидается JSON-объект {{имя слоя: класс}} ({e})") from e
    return _checked_layer_map(value)


def _checked_layer_map(value) -> Optional[dict[str, str]]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise HTTPException(400, "layerMap: ожидается JSON-объект {имя слоя: класс}")
    unknown = sorted({v for v in value.values() if v not in LAYER_KINDS})
    if unknown:
        raise HTTPException(400, f"layerMap: неизвестные классы {unknown}; список -- GET /api/layer-kinds")
    return value or None


class LayerKind(BaseModel):
    id: str
    label: str


@router.get(
    "/api/layer-kinds",
    response_model=list[LayerKind],
    tags=["Чертежи"],
    summary="Классы слоёв для назначения вручную",
)
async def get_layer_kinds():
    """Что можно назначить слою чертежа, если автоматическое распознавание
    ошиблось или не узнало слой: поле `layerMap` загрузки и `/api/reparse`."""
    return [LayerKind(id=kind, label=label) for kind, (_key, label) in LAYER_KINDS.items()]


@router.post(
    "/api/parse",
    tags=["Чертежи"],
    summary="Загрузить чертёж DXF",
    responses={**SCENE_RESPONSE, **HEAVY_RESPONSES},
    dependencies=[rate_limit("parse")],
)
async def parse_dxf_endpoint(
    file: UploadFile = File(..., description="Чертёж участка в формате DXF."),
    layerMap: Optional[str] = Form(default=None, description=LAYER_MAP_DESCRIPTION),  # noqa: N803 -- имя поля API
):
    """Разбирает DXF по слоям: граница участка, здания, дороги, дорожки,
    подземные сети и охранные зоны, существующие посадки и малые формы.
    Слои распознаются по имени (с синонимами), по содержимому (имя блока,
    образец штриховки, тип линии); вставки блоков раскрываются. Что взято из
    каждого слоя -- в `meta.layers`, замечания разбора -- в `meta.warnings`.
    Координаты сцены -- метры от центра участка. Исходный файл сохраняется
    на сервере (`meta.sourceId`): экспорт допишет результат поверх него."""
    if not (file.filename or "").lower().endswith(".dxf"):
        raise HTTPException(400, "Ожидается файл .dxf")

    layer_map = _layer_map(layerMap)
    data = await file.read()
    scene = await run_heavy(_parse_dxf, data, file.filename, layer_map)
    metrics.dxf_parses_total.inc()
    log.info("разобран %r: %d объектов, %d зон", file.filename, len(scene.get("objects", [])), len(scene.get("restrictions", [])))
    return scene


@router.post(
    "/api/parse-dwg",
    tags=["Чертежи"],
    summary="Загрузить комплект чертежей: папку DWG/DXF или zip",
    responses={**SCENE_RESPONSE, **HEAVY_RESPONSES},
    dependencies=[rate_limit("parse_dwg")],
)
async def parse_dwg_folder_endpoint(
    files: list[UploadFile] = File(
        ..., description="Чертежи объекта: .dwg, .dxf и zip-архивы с ними; остальные файлы папки игнорируются."
    ),
    layerMap: Optional[str] = Form(default=None, description=LAYER_MAP_DESCRIPTION),  # noqa: N803 -- имя поля API
):
    """Комплект чертежей объекта, как его присылает заказчик: топосъёмка,
    сети, рамки с границей заказа, генплан с внешними ссылками -- файлами
    .dwg и .dxf или zip-архивом (архивы распаковываются, служебные файлы
    архиваторов пропускаются). Каждый DWG конвертируется в DXF (LibreDWG),
    все чертежи сливаются в один и разбираются так же, как `/api/parse`.
    Файл, вставленный другим файлом комплекта внешней ссылкой со сдвигом или
    поворотом, кладётся так, как стоит вставка. Если в комплекте есть DWG,
    отдельные DXF не берутся (обычно это выгруженная копия). Файлы, которые
    не удалось прочитать или которые пропущены, не прерывают загрузку: они
    перечислены в `dwgConversionWarnings`. Разбор идёт в отдельном процессе;
    одновременно -- не больше `DWG_MAX_PARALLEL` комплектов, остальные ждут
    очереди."""
    layer_map = _layer_map(layerMap)
    uploads = [f for f in files if Path(f.filename or "").suffix.lower() in PACKAGE_UPLOAD_SUFFIXES]
    if not uploads:
        raise HTTPException(400, "Среди загруженных файлов нет ни одного .dwg, .dxf или .zip")

    tmp_path = Path(await run_light(tempfile.mkdtemp))
    try:
        saved = await _save_uploads(uploads, tmp_path)
        selection = await run_light(_unpack_package, saved, tmp_path)
        if not selection.drawings:
            raise HTTPException(400, "В комплекте нет ни одного чертежа .dwg или .dxf")
        if len(selection.drawings) > MAX_DWG_FILES:
            raise HTTPException(400, f"Не больше {MAX_DWG_FILES} чертежей за одну загрузку")

        source_id = await run_light(source_store.save_dwg_batch, selection.drawings)
        payload, summary = await _run_dwg_job(selection.drawings, tmp_path, source_id, layer_map, selection.skipped)
    finally:
        await run_light(shutil.rmtree, tmp_path, True)

    metrics.dwg_batch_conversions_total.labels(outcome="success").inc()
    metrics.dwg_files_processed_total.labels(outcome="converted").inc(summary["converted"])
    metrics.dwg_files_processed_total.labels(outcome="failed").inc(summary["failed"])
    metrics.dxf_parses_total.inc()
    log.info(
        "комплект чертежей разобран: %d/%d файлов, %d объектов, %d зон",
        summary["converted"], len(selection.drawings), summary["objects"], summary["zones"],
    )
    # JSON из дочернего процесса отдаётся как есть, без повторного разбора.
    return Response(content=payload, media_type="application/json")


PACKAGE_UPLOAD_SUFFIXES = (*drawing_package.DRAWING_SUFFIXES, drawing_package.ARCHIVE_SUFFIX)


async def _save_uploads(uploads: list[UploadFile], folder: Path) -> list[Path]:
    """Файлы формы -- в folder под своими именами (без папок: .name отрезает
    «../»); одноимённые из разных папок не перезаписывают друг друга."""
    saved = []
    for f in uploads:
        dest = drawing_package.unique_path(folder, Path(f.filename).name)
        await run_light(dest.write_bytes, await f.read())
        saved.append(dest)
    return saved


def _unpack_package(paths: list[Path], folder: Path) -> drawing_package.PackageSelection:
    """Архивы распаковываются в folder; что пойдёт в сборку -- по правилу
    drawing_package.select_drawings."""
    drawings = []
    for path in paths:
        if path.suffix.lower() == drawing_package.ARCHIVE_SUFFIX:
            unpacked = drawing_package.unique_path(folder, f"{path.stem}.unzipped")
            unpacked.mkdir()
            try:
                drawings += drawing_package.extract_drawings(path, unpacked)
            except drawing_package.PackageError as e:
                raise HTTPException(400, str(e)) from e
            path.unlink()
        else:
            drawings.append(path)
    return drawing_package.select_drawings(drawings)


async def _run_dwg_job(
    dwg_paths: list[Path],
    work_dir: Path,
    source_id: Optional[str],
    layer_map: Optional[dict[str, str]],
    notes: Optional[dict[str, str]] = None,
) -> tuple[bytes, dict]:
    job_kwargs = {"layer_map": layer_map} if layer_map else {}
    if notes:
        job_kwargs["notes"] = notes
    try:
        async with dwg_job.slot():
            return await dwg_job.run(dwg_paths, work_dir, request_id=current_request_id(), source_id=source_id, **job_kwargs)
    except dwg_job.DwgJobError as e:
        metrics.dwg_batch_conversions_total.labels(outcome=e.outcome).inc()
        if e.failed:
            metrics.dwg_files_processed_total.labels(outcome="failed").inc(e.failed)
        if e.outcome == "parse_error":
            metrics.dxf_parse_errors_total.inc()
        raise HTTPException(e.status, e.detail) from e


class ReparseRequest(BaseModel):
    sourceId: str = Field(description="meta.sourceId загруженной сцены.")
    layerMap: dict[str, str] = Field(default={}, description="Назначения слоёв {имя слоя: класс}; пустой -- только автоматическое распознавание.")


@router.post(
    "/api/reparse",
    tags=["Чертежи"],
    summary="Разобрать загруженный чертёж заново с назначениями слоёв",
    responses={**SCENE_RESPONSE, **HEAVY_RESPONSES, 404: {"description": "Исходника на сервере нет -- загрузите чертёж заново."}},
    dependencies=[rate_limit("parse")],
)
async def reparse_endpoint(request: ReparseRequest):
    """Тот же разбор, что при загрузке (DXF или пачка DWG), исходник берётся
    с сервера по `sourceId` -- загружать файлы второй раз не нужно. Так
    применяются назначения слоёв из окна «Слои чертежа»."""
    layer_map = _checked_layer_map(request.layerMap)
    found = await run_light(source_store.find, request.sourceId)
    if found is None:
        raise HTTPException(404, "Исходного чертежа на сервере нет -- загрузите его заново.")
    kind, source = found
    if kind == "dxf":
        data = await run_light(source.read_bytes)
        scene = await run_heavy(_parse_dxf, data, source.name, layer_map)
        metrics.dxf_parses_total.inc()
        return scene
    tmp_path = Path(await run_light(tempfile.mkdtemp))
    try:
        payload, _summary = await _run_dwg_job(source, tmp_path, request.sourceId, layer_map)
    finally:
        await run_light(shutil.rmtree, tmp_path, True)
    metrics.dxf_parses_total.inc()
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
    doc = read_dxf(path)
    summary = overlay_scene(scene, doc)
    stamp_versions(doc, scene.meta.sourceSha256)
    buf = io.StringIO()
    doc.write(buf)
    logging.getLogger("greencity.export").info("экспорт поверх исходного DXF: %s", summary.layers)
    return buf.getvalue().encode("utf-8")


def _rebuilt_dxf(scene: Scene) -> bytes:
    buf = io.StringIO()
    doc = scene_to_dxf(scene)
    stamp_versions(doc, scene.meta.sourceSha256)
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


async def _overlay_dwg(paths: list[Path], scene: Scene) -> bytes:
    tmp_path = Path(await run_light(tempfile.mkdtemp))
    try:
        async with dwg_job.slot():
            return await dwg_job.run_export(paths, tmp_path, scene, request_id=current_request_id())
    finally:
        await run_light(shutil.rmtree, tmp_path, True)


async def _export(scene: Scene) -> tuple[bytes, str, Optional[tuple[str, Path | list[Path]]]]:
    """(DXF, режим: overlay -- поверх исходника, rebuilt -- из сцены, исходник
    из хранилища или None)."""
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
    return content, mode, found if mode == "overlay" else None


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
    в файле объяснений. В заголовке файла -- SHA-256 исходника и версии
    правил (`GreenCity.*`). Если исходника на сервере нет, DXF собирается из
    сцены (метры от центра участка). Проверка выгрузки -- `/api/export-dxf/verify`."""
    content, mode, _found = await _export(scene)
    metrics.dxf_exports_total.inc()
    logging.getLogger("greencity.export").info("экспортирована сцена (%s): %d объектов", mode, len(scene.objects))
    return Response(
        content=content,
        media_type="application/dxf",
        headers={"Content-Disposition": 'attachment; filename="greencity_plan.dxf"', "X-GreenCity-Export": mode},
    )


def _verify(scene: Scene, content: bytes, mode: str, found) -> ExportVerification:
    if found and found[0] == "dxf":
        return verify_export(scene, content, mode, source_doc=read_dxf(found[1]), source_sha256=sha256_of(found[1].read_bytes()))
    if found and found[0] == "dwg":
        # Комплект собирается детерминированно: собранный заново -- тот же
        # исходник, что лёг под слои результата при экспорте.
        with tempfile.TemporaryDirectory() as work:
            try:
                merge_kwargs = {"layer_map": scene.meta.layerMap} if scene.meta.layerMap else {}
                merged = dwg_batch_converter.merge_dwg_files(found[1], Path(work), **merge_kwargs).doc
            except Exception as e:  # noqa: BLE001 -- без исходника проверяются нормы и места
                note = f"комплект файлов не собран заново ({e}) — отпечатки сущностей не сравниваются"
                return verify_export(scene, content, mode, source_sha256=sha256_of_files(found[1]), source_note=note)
            return verify_export(scene, content, mode, source_doc=merged, source_sha256=sha256_of_files(found[1]))
    return verify_export(scene, content, mode, source_sha256=scene.meta.sourceSha256)


@router.post(
    "/api/export-dxf/verify",
    response_model=ExportVerification,
    tags=["Чертежи"],
    summary="Независимая проверка выгрузки DXF",
    responses=HEAVY_RESPONSES,
    dependencies=[rate_limit("export")],
)
async def verify_export_endpoint(scene: Scene):
    """Тот же DXF, что отдаёт `/api/export-dxf`, читается заново и
    проверяется без опоры на сцену в памяти: сущности исходного чертежа
    целы (сравниваются отпечатки по handle), новые посадки стоят на своих
    местах, и по геометрии из файла у них нет нарушений норм отступов. В
    отчёте -- SHA-256 исходника и результата и версии правил."""
    content, mode, found = await _export(scene)
    report = await run_heavy(_verify, scene, content, mode, found)
    logging.getLogger("greencity.export").info(
        "проверка выгрузки (%s): исходник %s, посадок %d, нарушений %d",
        mode, report.source_check, report.plants_checked, len(report.violations),
    )
    return report
