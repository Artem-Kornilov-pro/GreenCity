"""
Текстовое редактирование плана озеленения через LLM (ТЗ: "возможность внесения
корректировок в текстовом и графическом формате" -- графический формат это
3D-редактор, текстовый -- этот модуль).

LLM НЕ возвращает сцену целиком: сцена бывает огромной (локация 5 -- больше
тысячи объектов и 2000+ зон), такой JSON не влезает в ответ, а модель
гарантированно испортит в нём координаты. Модель возвращает короткий список
операций, а этот модуль применяет их детерминированно. Модель предлагает --
код решает. Этот же словарь операций ложится один в один на инструменты
будущего MCP-сервера.

Задача разбита на два уровня:
* групповые операции (place_along, place_in_area, remove_where) -- модель
  выбирает намерение, виды и параметры, а координаты считает геометрический
  планировщик (placement.py). Так выполняется всё про ряды, аллеи, "вдоль",
  "по периметру", "засадить";
* точечные операции (add, remove, move, rotate) -- для конкретных объектов.
  Точку с нарушением норм планировщик сдвигает в ближайшее допустимое место.

Почему так: в первой версии модель сама считала координаты каждого куста и на
"размести кустарники вдоль дорожек" поставила 5 кустов -- на дорожке, на
парковке, у стены и вплотную к фонарям, хотя контуры дорожек были в контексте
полностью. Смещённая линия, шаг и проверка сотни точек -- работа для кода, а
не для языковой модели.

Настройки берутся из переменных окружения (.env в корне репозитория локально,
env_file в docker-compose): LLM_PROVIDER выбирает поставщика ("gemini" по
умолчанию или "yandex"). Для yandex: YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER,
YANDEX_CLOUD_MODEL. Для gemini: GEMINI_API_KEY, GEMINI_MODEL. Оба провайдера
доступны через OpenAI-совместимый API (openai-клиент с другим base_url), но
Gemini поддерживает только Chat Completions, а не Responses API, которым уже
пользуется yandex-путь -- поэтому запрос к модели устроен как два отдельных
метода (_call_responses/_call_chat_completions) под одной _client_and_model.

Устройство по файлам:
* text_editor/operations.py -- словарь операций (pydantic-модели ответа модели);
* text_editor/prompt.py -- контекст участка и системный промпт;
* llm_client.py -- выбор провайдера, запрос, разбор ответа в LlmPlan;
* text_editor/applier.py (+ text_editor/ops_placement.py, text_editor/ops_editing.py,
  plan_common.py) -- детерминированное применение операций;
* этот модуль -- точка входа edit_scene_with_text для /api/edit-with-text.
"""

from __future__ import annotations

import json
import logging
import time

from core.placement import Placer
from core.plant_catalog import load_catalog
from core.schemas import Scene
from text_editor.applier import apply_plan
from text_editor.llm_client import LlmError, LlmNotConfiguredError, request_plan
from text_editor.operations import LlmPlan, TextEditRequest, TextEditResult

__all__ = [
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


def edit_scene_with_text(scene: Scene, instruction: str) -> TextEditResult:
    catalog = load_catalog()
    # Один планировщик на запрос: допустимые области, построенные для
    # контекста модели, переиспользуются при применении плана.
    placer = Placer(scene)
    plan = request_plan(scene, instruction, catalog, placer)
    logger.info("план: %s", json.dumps(plan.operations, ensure_ascii=False)[:1500])

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
