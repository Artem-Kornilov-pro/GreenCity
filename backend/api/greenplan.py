"""
Эндпоинты GreenPlan: озеленение участка по похожим проектам, текст-обоснование
решений, объяснения посадок и пояснительная записка.

Расчёты идут в пуле потоков с ограничением очереди (core/concurrency.py):
геометрия на shapely упирается в GIL, и параллельные тяжёлые запросы в одном
процессе не ускоряются, а тормозят друг друга. Текст-обоснование -- асинхронный
запрос к модели, слота не занимает.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from api.protection import HEAVY_RESPONSES, rate_limit
from core.concurrency import run_heavy
from core.plant_catalog import catalog_by_id
from core.schemas import Scene
from greenplan.assortment_report import AssortmentRow, improvements_assortment, lawn_assortment, summarize_assortment
from greenplan.decision_report import DecisionReportUnavailable, generate_report
from greenplan.document import build_document
from greenplan.excel_report import explanations_xlsx
from greenplan.explanations import RejectionStats, explain_plants, explanations_csv, explanations_json
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pipeline import run_greenplan
from greenplan.violation_report import Violation, find_violations
from greenplan.visualization import NOTE as VISUALIZATION_NOTE
from greenplan.visualization import VIEWS, VisualizationFailed, VisualizationNotConfigured, generate_visualizations
from monitoring import metrics

router = APIRouter()
log = logging.getLogger("greencity.greenplan")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class GreenPlanGenerateRequest(BaseModel):
    scene: Scene = Field(description="Сцена участка из /api/parse или /api/parse-dwg, можно с правками.")
    options: GreenPlanOptions = Field(default_factory=GreenPlanOptions, description="Параметры озеленения.")


class GreenPlanGenerateResult(BaseModel):
    scene: Scene = Field(description="Сцена с новой посадкой, газоном и благоустройством.")
    assignments: list[ZoneAssignment] = Field(description="Решение по каждой геометрической зоне участка.")
    violations: list[Violation] = Field(description="Нарушения норм отступов на участке (исходные объекты и новые).")
    assortment: list[AssortmentRow] = Field(description="Ведомость новой посадки по видам, газон -- в м².")
    improvements: list[AssortmentRow] = Field(default=[], description="Ведомость благоустройства: дорожки, фонари, скамейки, урны.")
    notes: list[str] = Field(default=[], description="Что из параметров не удалось выполнить и почему.")
    rejections: RejectionStats = Field(
        default_factory=RejectionStats,
        description="Точки, отклонённые по нормам при расстановке; передайте их в /api/greenplan/explanations.",
    )


class GreenPlanReportResult(BaseModel):
    report: Optional[str] = Field(description="Текст-обоснование решений или null.")
    report_error: Optional[str] = Field(description="Почему текст не получен (нет ключа, модель недоступна) или null.")


class GreenPlanVisualizationsRequest(BaseModel):
    scene: Scene = Field(description="Сцена с результатом /api/greenplan/generate.")
    assignments: list[ZoneAssignment] = Field(description="Решения по зонам из /api/greenplan/generate.")
    views: list[str] = Field(
        default=list(VIEWS), description=f"Ракурсы: {', '.join(f'{k} -- {v.lower()}' for k, v in VIEWS.items())}."
    )
    seed: int = Field(default=1, ge=0, le=2**31 - 1, description="Зерно генерации: другое -- другие картинки.")

    @field_validator("views")
    @classmethod
    def _known_views(cls, value):
        unknown = [v for v in value if v not in VIEWS]
        if unknown or not value or len(value) > len(VIEWS):
            raise ValueError(f"ракурсы -- от 1 до {len(VIEWS)} из {list(VIEWS)}; неизвестные: {unknown}")
        return list(dict.fromkeys(value))


class GreenPlanVisualization(BaseModel):
    view: str
    prompt: str = Field(description="Что попросили нарисовать -- по решениям GreenPlan.")
    image: str = Field(description="Изображение в base64 (JPEG; PNG -- если так отдаст модель).")


class GreenPlanVisualizationsResult(BaseModel):
    note: str = Field(description="Пометка для показа рядом с картинками.")
    visualizations: list[GreenPlanVisualization]


class GreenPlanDocumentRequest(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment]
    report: Optional[str] = Field(default=None, description="Текст из /api/greenplan/report -- идёт приложением с пометкой «текст ИИ».")
    title: Optional[str] = Field(default=None, description="Название участка для титула записки.")
    options: Optional[GreenPlanOptions] = Field(default=None, description="Параметры запуска -- в раздел «Принятые решения».")
    notes: list[str] = []


class GreenPlanExplanationsRequest(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment] = []
    rejections: RejectionStats = Field(default_factory=RejectionStats)


@router.post(
    "/api/greenplan/generate",
    response_model=GreenPlanGenerateResult,
    tags=["GreenPlan"],
    summary="Озеленить участок",
    responses=HEAVY_RESPONSES,
    dependencies=[rate_limit("greenplan")],
)
async def greenplan_generate(
    request: GreenPlanGenerateRequest,
    k: int = Query(default=3, ge=1, le=9, description="Сколько похожих проектов голосуют за стиль и приёмы."),
):
    """Проектирует озеленение участка по реализованным проектам-аналогам:
    стиль участка и единая палитра видов, приём для каждой зоны, расстановка
    с проверкой норм отступов в каждой точке, газон на свободной земле.
    Работает без LLM, за секунды. Повторный запуск заменяет прошлый результат.
    Алгоритм -- в docs/GREENPLAN_ALGORITHM.md."""
    return await run_heavy(_generate, request, k)


def _generate(request: GreenPlanGenerateRequest, k: int) -> GreenPlanGenerateResult:
    run = run_greenplan(request.scene, request.options, k)
    log.info(
        "GreenPlan: %d посадок, %d объектов и %d дорожек благоустройства, %d зон, газон %d участков, "
        "удалено %d насаждений с нарушением норм",
        len(run.new_plants), len(run.improvements.objects), len(run.improvements.zones),
        len(run.assignments), len(run.scene.lawns), len(run.removed_plants),
    )
    return GreenPlanGenerateResult(
        scene=run.scene,
        assignments=run.assignments,
        violations=find_violations(run.scene),
        assortment=summarize_assortment(run.new_plants, catalog_by_id()) + lawn_assortment(run.scene.lawns),
        improvements=improvements_assortment(run.scene),
        notes=run.notes,
        rejections=run.rejections,
    )


@router.post(
    "/api/greenplan/visualizations",
    response_model=GreenPlanVisualizationsResult,
    tags=["GreenPlan"],
    summary="Фотореалистичные визуализации плана (ИИ)",
    responses={429: HEAVY_RESPONSES[429], 502: {"description": "Alice AI ART не вернул изображение."}, 503: {"description": "Ключ Yandex AI Studio не задан."}},
    dependencies=[rate_limit("llm")],
)
async def greenplan_visualizations(request: GreenPlanVisualizationsRequest):
    """Бонусное задание ТЗ: иллюстрации участка после озеленения (Alice AI ART).
    Картинка рисуется по описанию решений GreenPlan -- стиль, приёмы, породы,
    газон, -- а не по чертежу, поэтому это иллюстрация замысла, а не точная
    расстановка (пометка `note`). Ракурсы -- `views`, до трёх; генерация --
    десятки секунд."""
    try:
        images = await generate_visualizations(request.scene, request.assignments, request.views, request.seed)
    except VisualizationNotConfigured as e:
        raise HTTPException(503, str(e)) from e
    except (VisualizationFailed, httpx.HTTPError) as e:
        log.warning("визуализации не получены: %s", e)
        raise HTTPException(502, f"Визуализации не получены: {e}") from e
    log.info("визуализации GreenPlan: %d ракурсов", len(images))
    return GreenPlanVisualizationsResult(
        note=VISUALIZATION_NOTE,
        visualizations=[
            GreenPlanVisualization(view=v.view, prompt=v.prompt, image=base64.b64encode(v.image_jpeg).decode("ascii")) for v in images
        ],
    )


@router.post(
    "/api/greenplan/report",
    response_model=GreenPlanReportResult,
    tags=["GreenPlan"],
    summary="Текст-обоснование решений",
    responses={429: HEAVY_RESPONSES[429]},
    dependencies=[rate_limit("llm")],
)
async def greenplan_report(assignments: list[ZoneAssignment]):
    """Связный текст для заказчика по решениям из `/api/greenplan/generate`
    (YandexGPT). Модель только пересказывает готовые решения и ничего не
    добавляет от себя. Если модель недоступна, ответ 200 с `report_error`."""
    try:
        report = await generate_report(assignments)
    except DecisionReportUnavailable as e:
        metrics.greenplan_generate_total.labels(report_outcome="unavailable").inc()
        return GreenPlanReportResult(report=None, report_error=str(e))
    metrics.greenplan_generate_total.labels(report_outcome="success").inc()
    return GreenPlanReportResult(report=report, report_error=None)


@router.post(
    "/api/greenplan/document",
    tags=["GreenPlan"],
    summary="Пояснительная записка (DOCX)",
    responses={200: {"content": {DOCX_MEDIA_TYPE: {}}, "description": "Документ Word."}, **HEAVY_RESPONSES},
    dependencies=[rate_limit("greenplan")],
)
async def greenplan_document(request: GreenPlanDocumentRequest):
    """Пояснительная записка к проекту озеленения: сведения об участке,
    нормативная база и применённые отступы, принятые решения с
    происхождением, ведомость элементов озеленения по форме 9 ГОСТ 21.508,
    проверка норм, технические требования и ограничения. Нарушения и
    ведомость пересчитываются по присланной сцене."""
    content = await run_heavy(
        build_document,
        request.scene,
        request.assignments,
        report=request.report,
        title=request.title,
        options=request.options,
        notes=request.notes,
    )
    metrics.greenplan_documents_total.inc()
    log.info("выгружена пояснительная записка: %d зон", len(request.assignments))
    # filename* по RFC 5987 -- кириллица в обычном filename ломает часть браузеров.
    name = quote(f"Пояснительная записка — {request.title or 'участок'}.docx")
    return Response(
        content=content,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"greenplan_note.docx\"; filename*=UTF-8''{name}"},
    )


@router.post(
    "/api/greenplan/explanations",
    tags=["GreenPlan"],
    summary="Объяснения посадок со ссылками на НПА",
    responses={
        200: {
            "content": {
                "application/json": {}, "text/csv": {}, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            },
            "description": "Файл объяснений.",
        },
        **HEAVY_RESPONSES,
    },
    dependencies=[rate_limit("greenplan")],
)
async def greenplan_explanations(
    request: GreenPlanExplanationsRequest,
    format: str = Query(
        default="json",
        pattern="^(json|csv|xlsx)$",
        description="json, csv (разделитель «;», UTF-8 с BOM) или xlsx: ведомость, посадки, проверки, отклонённые, запрет посадки, сведения.",
    ),
):
    """Для каждой новой посадки: вид, координаты в сцене и в исходном
    чертеже, слой DXF, приём и проект-аналог, основание подбора вида и
    ближайшие ограничения с фактическим расстоянием, нормой и пунктом НПА.
    Отдельно -- отклонённые по нормам точки и зоны запрета посадки. id
    посадки совпадает с XDATA сущности в выгруженном DXF."""
    content, media, ext, count = await run_heavy(_explanations, request, format)
    log.info("выгружены объяснения посадок (%s): %d посадок", format, count)
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="greenplan_explanations.{ext}"'},
    )


def _explanations(request: GreenPlanExplanationsRequest, format: str) -> tuple[bytes, str, str, int]:
    plants = explain_plants(request.scene, request.assignments)
    if format == "xlsx":
        content = explanations_xlsx(request.scene, plants, request.rejections)
        return content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", len(plants)
    if format == "csv":
        # BOM -- чтобы Excel сразу распознал UTF-8.
        content = ("\ufeff" + explanations_csv(request.scene, plants, request.rejections)).encode("utf-8")
        return content, "text/csv; charset=utf-8", "csv", len(plants)
    data = explanations_json(request.scene, plants, request.rejections)
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), "application/json", "json", len(plants)
