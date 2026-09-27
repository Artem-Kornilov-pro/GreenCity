"""
Эндпоинты GreenPlan (issue #23): автоозеленение по прошлым проектам
(/generate, без LLM), текст-объяснение решений через YandexGPT
(/report) и пояснительная записка в DOCX (/document).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

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


class GreenPlanGenerateRequest(BaseModel):
    scene: Scene
    # Параметры пользователя (диалог GreenPlan): стиль, что сажать,
    # предпочтительные виды, благоустройство. Без них -- поведение по умолчанию.
    options: GreenPlanOptions = GreenPlanOptions()


class GreenPlanGenerateResult(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment]
    violations: list[Violation]
    assortment: list[AssortmentRow]
    # Ведомость благоустройства: новые дорожки (м²), фонари, скамейки, урны.
    improvements: list[AssortmentRow] = []
    # Для пользователя: что из параметров не удалось выполнить и почему
    # (предпочтительный вид не по нормам, нет двора под дорожки и т.п.).
    notes: list[str] = []
    # Точки, отклонённые по нормам при расстановке, -- для файла объяснений
    # (/api/greenplan/explanations): фронтенд возвращает их туда как есть.
    rejections: RejectionStats = RejectionStats()


# Синхронный def -- расстановка, нарушения и ведомость -- чистая
# CPU-геометрия, ни одного await. НАМЕРЕННО без текста-отчёта (LLM) -- тот
# вынесен в отдельный /api/greenplan/report ниже: вызов LLM занимает секунды
# (а с локальной моделью -- до ~30 с), и фронтенд не должен ждать его ради
# уже готовой расстановки.
@router.post("/api/greenplan/generate", response_model=GreenPlanGenerateResult)
def greenplan_generate(
    request: GreenPlanGenerateRequest,
    k: int = Query(default=3, ge=1, le=9, description="Число ближайших проектов-соседей для retrieval."),
):
    """Автоозеленение по прошлым проектам (GreenPlan, issue #23, Этапы 3-6)
    с параметрами пользователя (greenplan/options.py). Сначала общее решение
    на участок (стиль, палитра видов), потом приёмы зон; благоустройство --
    до посадок. Повторный запуск заменяет прошлый результат GreenPlan в
    присланной сцене, а не добавляет второй слой (greenplan/pipeline.py).

    Текст-объяснение -- отдельным запросом, см. /api/greenplan/report ниже:
    он ждёт ответа LLM и не должен блокировать уже
    готовый детерминированный результат.
    """
    with _greenplan_generate_lock:
        run = run_greenplan(request.scene, request.options, k)
        logging.getLogger("greencity.greenplan").info(
            "GreenPlan: %d новых посадок, %d объектов и %d дорожек благоустройства, %d зон, газон %d участков",
            len(run.new_plants), len(run.improvements.objects), len(run.improvements.zones),
            len(run.assignments), len(run.scene.lawns),
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


class GreenPlanReportResult(BaseModel):
    report: Optional[str]
    report_error: Optional[str]


# Синхронный def -- клиент openai блокирующий (как и у /api/edit-with-text
# ниже), FastAPI уводит в пул потоков; сам вызов -- секунды (YandexGPT),
# поэтому отдельный от /api/greenplan/generate эндпоинт: фронтенд
# показывает уже готовую расстановку сразу и дотягивает текст в фоне, не
# блокируя ничего остальным ожиданием LLM. Свой лок (не общий с generate
# выше) -- это разные ресурсы (CPU-геометрия vs сетевой вызов к LLM),
# нет причины заставлять их ждать друг друга.
@router.post("/api/greenplan/report", response_model=GreenPlanReportResult)
def greenplan_report(assignments: list[ZoneAssignment]):
    """Текст-объяснение решений GreenPlan (Этап 6) через LLM (YandexGPT в
    Yandex AI Studio, backend/greenplan/decision_report.py) -- по списку решений,
    уже посчитанному /api/greenplan/generate (передаётся сюда как есть, не
    пересчитывается). Недоступность LLM или нет ключа -- не ошибка запроса: 200 с
    report=None и понятной report_error, а не 500."""
    with _greenplan_report_lock:
        try:
            report = generate_report(assignments)
            metrics.greenplan_generate_total.labels(report_outcome="success").inc()
            return GreenPlanReportResult(report=report, report_error=None)
        except DecisionReportUnavailable as e:
            metrics.greenplan_generate_total.labels(report_outcome="unavailable").inc()
            return GreenPlanReportResult(report=None, report_error=str(e))


class GreenPlanDocumentRequest(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment]
    # Текст из /api/greenplan/report, если фронтенд его уже получил: идёт в
    # приложение с пометкой "текст ИИ -- проверить". Заново LLM не вызываем.
    report: Optional[str] = None
    title: Optional[str] = None
    # Параметры запуска и заметки из /generate -- в записку ("Параметры,
    # заданные пользователем"); без них записка -- как раньше.
    options: Optional[GreenPlanOptions] = None
    notes: list[str] = []


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# Синхронный def -- characterize_site и find_violations -- CPU-геометрия,
# под тем же локом, что и генерация (см. _greenplan_generate_lock выше).
@router.post("/api/greenplan/document")
def greenplan_document(request: GreenPlanDocumentRequest):
    """Пояснительная записка в DOCX ("сценарий выгрузки/генерации
    документации для внедрения предложенного решения" из ТЗ) по сцене
    с результатом /api/greenplan/generate и его решениям по зонам. Нарушения,
    ведомость и характеристики участка пересчитываются здесь по присланной
    сцене, а не берутся с фронтенда на веру."""
    with _greenplan_generate_lock:
        content = build_document(
            request.scene, request.assignments, report=request.report, title=request.title,
            options=request.options, notes=request.notes,
        )
    metrics.greenplan_documents_total.inc()
    logging.getLogger("greencity.greenplan").info("выгружена пояснительная записка: %d зон", len(request.assignments))
    # filename -- ASCII для старых клиентов, filename* -- с названием участка
    # по RFC 5987 (кириллица в обычном filename ломает часть браузеров).
    name = quote(f"Пояснительная записка — {request.title or 'участок'}.docx")
    return Response(
        content=content,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"greenplan_note.docx\"; filename*=UTF-8''{name}"},
    )


class GreenPlanExplanationsRequest(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment] = []
    rejections: RejectionStats = RejectionStats()


@router.post("/api/greenplan/explanations")
def greenplan_explanations(
    request: GreenPlanExplanationsRequest,
    format: str = Query(default="json", pattern="^(json|csv)$", description="json или csv (разделитель ;)"),
):
    """Объяснение КАЖДОЙ новой посадки со ссылкой на НПА и пункт (ТЗ, п. 8
    и 7.2.6): вид, место и приём, проект-аналог, основание подбора вида,
    ближайшие ограничения с фактическим расстоянием, нормой и пунктом;
    отклонённые по нормам точки и зоны запрета посадки. id посадки -- тот
    же, что в XDATA сущности выгруженного DXF (слои NEW_* / USER_*)."""
    with _greenplan_generate_lock:
        plants = explain_plants(request.scene, request.assignments)
        if format == "csv":
            # BOM -- чтобы Excel сразу открыл кириллицу в UTF-8.
            content = ("\ufeff" + explanations_csv(request.scene, plants, request.rejections)).encode("utf-8")
            media, ext = "text/csv; charset=utf-8", "csv"
        else:
            content = json.dumps(explanations_json(request.scene, plants, request.rejections), ensure_ascii=False, indent=1).encode("utf-8")
            media, ext = "application/json", "json"
    logging.getLogger("greencity.greenplan").info("выгружены объяснения посадок (%s): %d посадок", format, len(plants))
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="greenplan_explanations.{ext}"'},
    )
