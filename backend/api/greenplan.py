"""
Эндпоинты GreenPlan: озеленение участка по похожим проектам, текст-обоснование
решений, объяснения посадок и пояснительная записка.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.plant_catalog import catalog_by_id
from core.schemas import Scene
from greenplan.assortment_report import AssortmentRow, improvements_assortment, lawn_assortment, summarize_assortment
from greenplan.decision_report import DecisionReportUnavailable, generate_report
from greenplan.document import build_document
from greenplan.explanations import RejectionStats, explain_plants, explanations_csv, explanations_json
from greenplan.options import GreenPlanOptions
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pipeline import run_greenplan
from greenplan.violation_report import Violation, find_violations
from monitoring import metrics

router = APIRouter()
log = logging.getLogger("greencity.greenplan")

# Геометрия на shapely упирается в GIL: параллельные тяжёлые запросы в одном
# процессе не ускоряются, а тормозят друг друга. Лок выстраивает их в очередь.
_greenplan_generate_lock = threading.Lock()
_greenplan_report_lock = threading.Lock()

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
)
def greenplan_generate(
    request: GreenPlanGenerateRequest,
    k: int = Query(default=3, ge=1, le=9, description="Сколько похожих проектов голосуют за стиль и приёмы."),
):
    """Проектирует озеленение участка по реализованным проектам-аналогам:
    стиль участка и единая палитра видов, приём для каждой зоны, расстановка
    с проверкой норм отступов в каждой точке, газон на свободной земле.
    Работает без LLM, за секунды. Повторный запуск заменяет прошлый результат.
    Алгоритм -- в docs/GREENPLAN_ALGORITHM.md."""
    with _greenplan_generate_lock:
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
    "/api/greenplan/report",
    response_model=GreenPlanReportResult,
    tags=["GreenPlan"],
    summary="Текст-обоснование решений",
)
def greenplan_report(assignments: list[ZoneAssignment]):
    """Связный текст для заказчика по решениям из `/api/greenplan/generate`
    (YandexGPT). Модель только пересказывает готовые решения и ничего не
    добавляет от себя. Если модель недоступна, ответ 200 с `report_error`."""
    with _greenplan_report_lock:
        try:
            report = generate_report(assignments)
            metrics.greenplan_generate_total.labels(report_outcome="success").inc()
            return GreenPlanReportResult(report=report, report_error=None)
        except DecisionReportUnavailable as e:
            metrics.greenplan_generate_total.labels(report_outcome="unavailable").inc()
            return GreenPlanReportResult(report=None, report_error=str(e))


@router.post(
    "/api/greenplan/document",
    tags=["GreenPlan"],
    summary="Пояснительная записка (DOCX)",
    responses={200: {"content": {DOCX_MEDIA_TYPE: {}}, "description": "Документ Word."}},
)
def greenplan_document(request: GreenPlanDocumentRequest):
    """Пояснительная записка к проекту озеленения: сведения об участке,
    нормативная база и применённые отступы, принятые решения с
    происхождением, ведомость элементов озеленения по форме 9 ГОСТ 21.508,
    проверка норм, технические требования и ограничения. Нарушения и
    ведомость пересчитываются по присланной сцене."""
    with _greenplan_generate_lock:
        content = build_document(
            request.scene, request.assignments, report=request.report, title=request.title,
            options=request.options, notes=request.notes,
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
    responses={200: {"content": {"application/json": {}, "text/csv": {}}, "description": "Файл объяснений."}},
)
def greenplan_explanations(
    request: GreenPlanExplanationsRequest,
    format: str = Query(default="json", pattern="^(json|csv)$", description="json или csv (разделитель «;», UTF-8 с BOM)."),
):
    """Для каждой новой посадки: вид, координаты в сцене и в исходном
    чертеже, слой DXF, приём и проект-аналог, основание подбора вида и
    ближайшие ограничения с фактическим расстоянием, нормой и пунктом НПА.
    Отдельно -- отклонённые по нормам точки и зоны запрета посадки. id
    посадки совпадает с XDATA сущности в выгруженном DXF."""
    with _greenplan_generate_lock:
        plants = explain_plants(request.scene, request.assignments)
        if format == "csv":
            # BOM -- чтобы Excel сразу распознал UTF-8.
            content = ("﻿" + explanations_csv(request.scene, plants, request.rejections)).encode("utf-8")
            media, ext = "text/csv; charset=utf-8", "csv"
        else:
            data = explanations_json(request.scene, plants, request.rejections)
            content = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
            media, ext = "application/json", "json"
    log.info("выгружены объяснения посадок (%s): %d посадок", format, len(plants))
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="greenplan_explanations.{ext}"'},
    )
