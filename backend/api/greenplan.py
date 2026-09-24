"""
Эндпоинты GreenPlan (issue #23): автоозеленение по прошлым проектам
(/generate, без LLM), текст-объяснение решений через локальную LLM
(/report) и пояснительная записка в DOCX (/document).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from core.plant_catalog import catalog_by_id, load_catalog
from core.schemas import Scene
from greenplan.assortment_report import AssortmentRow, lawn_assortment, summarize_assortment
from greenplan.decision_report import DecisionReportUnavailable, generate_report
from greenplan.deterministic_placement import generate_for_scene
from greenplan.document import build_document
from greenplan.lawn import plan_lawns
from greenplan.pattern_assignment import ZoneAssignment
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
@router.post("/api/greenplan/generate", response_model=GreenPlanGenerateResult)
def greenplan_generate(scene: Scene, k: int = Query(default=3, ge=1, le=9, description="Число ближайших проектов-соседей для retrieval.")):
    """Автоозеленение по прошлым проектам (GreenPlan, issue #23, Этапы 3-6) --
    в отличие от /api/generate-greenery (сетка без понимания похожих
    проектов), здесь паттерн для каждой геометрической зоны участка выбирается
    по тому, что реально делали архитекторы на похожих участках из
    retrieval-корпуса (backend/greenplan/pattern_corpus.py), расстановка полностью
    детерминирована (backend/greenplan/deterministic_placement.py).

    Каталог видов не параметризуется с фронта -- берутся все деревья/кусты
    базового каталога (backend/core/plant_catalog.py) по категории.

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
        # Газон -- после посадок: клумбы кустарника из газона вычитаются.
        by_id = catalog_by_id()
        scene.lawns = plan_lawns(scene, by_id)

        logging.getLogger("greencity.greenplan").info(
            "GreenPlan: %d новых объектов, %d зон, газон %d участков", len(new_objects), len(assignments), len(scene.lawns)
        )

        return GreenPlanGenerateResult(
            scene=scene,
            assignments=assignments,
            violations=find_violations(scene),
            assortment=summarize_assortment(new_objects, by_id) + lawn_assortment(scene.lawns),
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
@router.post("/api/greenplan/report", response_model=GreenPlanReportResult)
def greenplan_report(assignments: list[ZoneAssignment]):
    """Текст-объяснение решений GreenPlan (Этап 6) через локальную LLM
    (mistral:7b/Ollama, backend/greenplan/decision_report.py) -- по списку решений,
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


class GreenPlanDocumentRequest(BaseModel):
    scene: Scene
    assignments: list[ZoneAssignment]
    # Текст из /api/greenplan/report, если фронтенд его уже получил: идёт в
    # приложение с пометкой "текст ИИ -- проверить". Заново LLM не вызываем.
    report: Optional[str] = None
    title: Optional[str] = None


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
        content = build_document(request.scene, request.assignments, report=request.report, title=request.title)
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
