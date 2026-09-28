"""
Правка плана озеленения текстом через LLM.

Модель возвращает не сцену, а короткий список операций, и этот модуль
применяет их детерминированно: модель предлагает -- код решает.
* Групповые операции (place_along, place_in_area, design_area, …): модель
  выбирает намерение, виды и параметры, координаты считает планировщик
  (core/placement.py).
* Точечные (add, remove, move, rotate): точку с нарушением норм планировщик
  сдвигает в ближайшее допустимое место.

Настройки -- из окружения: LLM_PROVIDER («yandex» по умолчанию или
«gemini»); для yandex -- YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER,
YANDEX_CLOUD_MODEL (по умолчанию qwen3-235b-a22b-fp8/latest); для gemini --
GEMINI_API_KEY, GEMINI_MODEL.

Файлы: operations.py -- словарь операций; prompt.py -- контекст и системный
промпт; llm_client.py -- запрос к модели; applier.py, ops_placement.py,
ops_editing.py, plan_common.py -- применение операций; здесь -- точка входа
edit_scene_with_text для /api/edit-with-text.

Геометрия (контекст для модели, применение плана) идёт в пуле потоков с
тяжёлым слотом (core/concurrency.py), запрос к модели -- асинхронно и слота
не держит: пока модель отвечает, сервер считает чужие запросы.
"""

from __future__ import annotations

import json
import logging
import time

from core.concurrency import run_heavy
from core.placement import Placer
from core.plant_catalog import CatalogItem, load_catalog
from core.schemas import Scene
from text_editor.applier import apply_plan
from text_editor.llm_client import (
    LlmError,
    LlmNotConfiguredError,
    ask_model,
    build_user_input,
    check_configured,
    request_plan,
)
from text_editor.operations import ChatTurn, LlmPlan, TextEditRequest, TextEditResult

__all__ = [
    "ChatTurn",
    "LlmError",
    "LlmNotConfiguredError",
    "LlmPlan",
    "TextEditRequest",
    "TextEditResult",
    "apply_plan",
    "edit_scene_with_text",
    "request_plan",
]

logger = logging.getLogger("greencity.llm")


def _prepare(scene: Scene, instruction: str, history: list[ChatTurn]) -> tuple[list[CatalogItem], Placer, str]:
    catalog = load_catalog()
    # Один планировщик на запрос: допустимые области, построенные для
    # контекста модели, переиспользуются при применении плана.
    placer = Placer(scene)
    return catalog, placer, build_user_input(scene, instruction, catalog, placer, history)


def _apply(scene: Scene, plan: LlmPlan, catalog: list[CatalogItem], placer: Placer) -> TextEditResult:
    started = time.monotonic()
    result = apply_plan(scene, plan, catalog, placer)
    logger.info(
        "операций %d за %.2f с: применено %d, отклонено %d, предупреждений %d",
        len(plan.operations),
        time.monotonic() - started,
        len(result.applied),
        len(result.rejected),
        len(result.warnings),
    )
    for line in result.applied:
        logger.info("  применено: %s", line)
    for reason in result.rejected:
        logger.info("  отклонено: %s", reason)
    return result


async def edit_scene_with_text(scene: Scene, instruction: str, history: list[ChatTurn] = ()) -> TextEditResult:
    check_configured()
    catalog, placer, user_input = await run_heavy(_prepare, scene, instruction, list(history))
    plan = await ask_model(user_input)
    logger.info("план: %s", json.dumps(plan.operations, ensure_ascii=False)[:1500])
    return await run_heavy(_apply, scene, plan, catalog, placer)
