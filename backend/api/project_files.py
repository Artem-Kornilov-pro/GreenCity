"""
Файлы проекта и доступ по ссылке (issue #150).

После GreenPlan фронтенд сохранённого проекта сам вызывает
POST /api/projects/{id}/greenplan-files: сервер сохраняет сцену и собирает
комплект -- пояснительную записку, объяснения посадок (JSON, CSV, Excel),
выгрузку DXF поверх исходника с отчётом её независимой проверки и
визуализации. Что не получилось (нет ключа ИИ, сбой выгрузки), -- заметкой;
остальное сохраняется. Следующий запуск заменяет комплект целиком.

Владелец открывает доступ по ссылке (/share/<ключ>): проект и его файлы
видны без входа, только для просмотра.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from accounts import project_files
from accounts import projects as projects_service
from accounts.auth import CurrentUser, require_user
from accounts.projects import NotFoundError
from accounts.schemas import ProjectFile, SharedProject, ShareResponse
from api.accounts import AUTH_ERRORS, StatusOk, _mongo_unavailable
from api.editor import _export, _verify
from api.greenplan import DOCX_MEDIA_TYPE, GreenPlanExplanationsRequest, _explanations
from api.protection import rate_limit
from core.concurrency import run_heavy
from core.schemas import Scene
from greenplan.document import build_document
from greenplan.explanations import RejectionStats
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.visualization import VisualizationFailed, VisualizationNotConfigured, generate_visualizations

router = APIRouter()
log = logging.getLogger("greencity.projects")

# Ракурс визуализации -- в имени файла.
VIEW_FILE_LABELS = {"overview": "общий вид", "pedestrian": "вид с тротуара", "detail": "крупный план"}
_EXPLANATION_KINDS = {"json": "explanations_json", "csv": "explanations_csv", "xlsx": "explanations_xlsx"}
NOT_FOUND = {404: {"description": "Проект или файл не найден."}}


class GreenPlanFilesRequest(BaseModel):
    scene: Scene = Field(description="Сцена с результатом GreenPlan -- сохраняется в проект.")
    assignments: list[ZoneAssignment]
    rejections: RejectionStats = Field(default_factory=RejectionStats)
    report: Optional[str] = Field(default=None, description="Текст-обоснование (/api/greenplan/report) -- приложением к записке.")
    options: Optional[GreenPlanOptions] = None
    notes: list[str] = []
    visualizations: bool = Field(default=True, description="Нарисовать визуализации (платные запросы к Alice AI ART).")


class GreenPlanFilesResult(BaseModel):
    files: list[ProjectFile] = Field(description="Все файлы проекта после замены комплекта.")
    notes: list[str] = Field(default=[], description="Что подготовить не удалось и почему.")


class ShareRequest(BaseModel):
    enabled: bool


def _http_errors(e: NotFoundError | PyMongoError) -> HTTPException:
    if isinstance(e, NotFoundError):
        return HTTPException(404, str(e))
    return _mongo_unavailable(e)


async def _store(project_id: str, name: str, kind: str, media_type: str, content: bytes) -> ProjectFile:
    return await asyncio.to_thread(project_files.store, project_id, name, kind, media_type, content)


async def _build_files(project_id: str, title: str, request: GreenPlanFilesRequest) -> tuple[list[ProjectFile], list[str]]:
    files: list[ProjectFile] = []
    notes: list[str] = []
    scene = request.scene

    async def attempt(what: str, make):
        try:
            await make()
        except Exception as e:  # noqa: BLE001 -- один сбой не отменяет остальной комплект
            log.warning("файлы проекта %s: %s не подготовлено: %s", project_id, what, e)
            notes.append(f"{what}: не подготовлено — {e}")

    async def note():
        content = await run_heavy(
            build_document, scene, request.assignments, report=request.report, title=title, options=request.options, notes=request.notes
        )
        files.append(await _store(project_id, f"Пояснительная записка — {title}.docx", "docx", DOCX_MEDIA_TYPE, content))

    async def explanations():
        body = GreenPlanExplanationsRequest(scene=scene, assignments=request.assignments, rejections=request.rejections)
        for fmt, kind in _EXPLANATION_KINDS.items():
            content, media, ext, _count = await run_heavy(_explanations, body, fmt)
            files.append(await _store(project_id, f"Объяснения посадок — {title}.{ext}", kind, media, content))

    async def drawing():
        content, mode, found = await _export(scene)
        files.append(await _store(project_id, f"Чертёж с озеленением — {title}.dxf", "dxf", "application/dxf", content))
        report = await run_heavy(_verify, scene, content, mode, found)
        verdict = "пройдена" if report.ok else "НЕ пройдена"
        files.append(
            await _store(
                project_id, f"Проверка выгрузки ({verdict}) — {title}.json", "export_check", "application/json", report.model_dump_json(indent=1).encode()
            )
        )

    async def pictures():
        try:
            images = await generate_visualizations(scene, request.assignments, list(VIEW_FILE_LABELS))
        except (VisualizationNotConfigured, VisualizationFailed, httpx.HTTPError) as e:
            raise RuntimeError(str(e)) from e
        for image in images:
            png = image.image_jpeg[:4] == b"\x89PNG"
            ext, media = ("png", "image/png") if png else ("jpg", "image/jpeg")
            label = VIEW_FILE_LABELS.get(image.view, image.view)
            files.append(await _store(project_id, f"Визуализация — {title} — {label}.{ext}", "visualization", media, image.image_jpeg))

    await attempt("Пояснительная записка", note)
    await attempt("Объяснения посадок", explanations)
    await attempt("Чертёж DXF и его проверка", drawing)
    if request.visualizations:
        await attempt("Визуализации", pictures)
    return files, notes


@router.post(
    "/api/projects/{project_id}/greenplan-files",
    response_model=GreenPlanFilesResult,
    tags=["Проекты"],
    summary="Сохранить результат GreenPlan и подготовить файлы проекта",
    responses={**AUTH_ERRORS, **NOT_FOUND},
    dependencies=[rate_limit("export")],
)
async def greenplan_files(project_id: str, request: GreenPlanFilesRequest, user: CurrentUser = Depends(require_user)):
    """Сохраняет сцену в проект и собирает комплект файлов по результату
    GreenPlan: записка, объяснения (JSON, CSV, Excel), выгрузка DXF поверх
    исходника и отчёт её проверки, визуализации. Прошлый комплект заменяется.
    На крупном объекте -- минуты (выгрузка и проверка DXF)."""
    try:
        project = await projects_service.update_project(user.id, project_id, None, request.scene)
        files, notes = await _build_files(project_id, project.name, request)
        all_files = await projects_service.replace_auto_files(user.id, project_id, files)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e
    log.info("файлы проекта %s: подготовлено %d, не удалось %d", project_id, len(files), len(notes))
    return GreenPlanFilesResult(files=all_files, notes=notes)


def _file_response(meta: ProjectFile, path) -> FileResponse:
    if not path.exists():
        raise HTTPException(404, "Файл проекта не найден на сервере")
    return FileResponse(path, media_type=meta.media_type, filename=meta.name)


@router.get(
    "/api/projects/{project_id}/files/{file_id}",
    tags=["Проекты"],
    summary="Скачать файл проекта",
    responses={**AUTH_ERRORS, **NOT_FOUND},
)
async def download_file(project_id: str, file_id: str, user: CurrentUser = Depends(require_user)):
    try:
        meta, path = await projects_service.get_file(user.id, project_id, file_id)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e
    return _file_response(meta, path)


@router.delete(
    "/api/projects/{project_id}/files/{file_id}",
    response_model=StatusOk,
    tags=["Проекты"],
    summary="Удалить файл проекта",
    responses={**AUTH_ERRORS, **NOT_FOUND},
)
async def delete_file(project_id: str, file_id: str, user: CurrentUser = Depends(require_user)):
    try:
        await projects_service.delete_file(user.id, project_id, file_id)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e
    return StatusOk()


@router.post(
    "/api/projects/{project_id}/share",
    response_model=ShareResponse,
    tags=["Проекты"],
    summary="Включить или выключить доступ по ссылке",
    responses={**AUTH_ERRORS, **NOT_FOUND},
)
async def share_project(project_id: str, request: ShareRequest, user: CurrentUser = Depends(require_user)):
    """enabled=true -- ключ ссылки /share/<ключ> (тот же, если доступ уже
    открыт); enabled=false -- ссылка перестаёт работать, следующее включение
    даст новую."""
    try:
        token = await projects_service.set_sharing(user.id, project_id, request.enabled)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e
    return ShareResponse(share_token=token)


@router.get("/api/shared/{token}", response_model=SharedProject, tags=["Проекты"], summary="Проект по ссылке", responses=NOT_FOUND)
async def shared_project(token: str):
    """Проект по ссылке -- без входа, только просмотр: сцена и файлы."""
    try:
        return await projects_service.get_shared(token)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e


@router.get("/api/shared/{token}/files/{file_id}", tags=["Проекты"], summary="Скачать файл проекта по ссылке", responses=NOT_FOUND)
async def shared_file(token: str, file_id: str):
    try:
        meta, path = await projects_service.get_shared_file(token, file_id)
    except (NotFoundError, PyMongoError) as e:
        raise _http_errors(e) from e
    return _file_response(meta, path)
