"""
Вызов LLM для правки текстом: провайдер по LLM_PROVIDER («yandex» по
умолчанию -- Yandex AI Studio, модель YANDEX_CLOUD_MODEL, по умолчанию Qwen3
235B; или «gemini»), запрос и разбор ответа в LlmPlan. Оба через
OpenAI-совместимый API: yandex -- Responses API, gemini -- Chat Completions.
Без ключей -- LlmNotConfiguredError (ответ 503).

Клиент асинхронный: пока модель думает, поток сервера свободен. Контекст для
модели строится синхронно (build_user_input) -- это геометрия, её вызывающий
код выполняет в пуле потоков.
"""

from __future__ import annotations

import json
import logging
import os
import time

import openai
from pydantic import ValidationError

from core import yandex_ai
from core.placement import (
    Placer,
)
from core.plant_catalog import CatalogItem
from core.schemas import Scene
from text_editor.operations import ChatTurn, LlmPlan
from text_editor.prompt import INSTRUCTIONS, _build_context

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
TEMPERATURE = 0.3
# Ответ модели занимает секунды; без явного таймаута openai-SDK ждёт до 10 минут
# и повторяет запрос дважды.
REQUEST_TIMEOUT_S = 120.0
MAX_RETRIES = 1
# С запасом под рассуждающие модели: у них рассуждение и ответ идут в один
# лимит, и при 2000 ответ обрывался пустым.
MAX_OUTPUT_TOKENS = 8000

# Сколько прошлых правок чата уходит модели и насколько коротко: для отсылок
# («убери их», «там же») хватает последних просьб и их итогов.
MAX_HISTORY_TURNS = 4
MAX_HISTORY_TEXT = 300
MAX_HISTORY_APPLIED = 4
MAX_HISTORY_APPLIED_TEXT = 160
# id новых объектов прошлой правки -- для «убери их». Больше -- уже не «их»,
# а заметная часть участка; тогда модель фильтрует по месту и виду.
MAX_HISTORY_IDS = 60


class LlmNotConfiguredError(RuntimeError):
    """Не заданы ключи LLM -- сервис работает, но текстовые правки недоступны."""


class LlmError(RuntimeError):
    """LLM недоступна или вернула ответ, который не удалось разобрать."""

# --- Вызов модели -----------------------------------------------------------


def _llm_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "yandex").strip().lower()


def check_configured() -> None:
    """LlmNotConfiguredError, если ключей выбранного провайдера нет. Проверка
    до сборки контекста: без ключа считать геометрию незачем."""
    if _llm_provider() == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            logger.warning("не задан GEMINI_API_KEY")
            raise LlmNotConfiguredError(
                "Текстовое редактирование не настроено: задайте GEMINI_API_KEY (см. .env.example)."
            )
    elif yandex_ai.credentials() is None:
        logger.warning("не заданы YANDEX_CLOUD_API_KEY / YANDEX_CLOUD_FOLDER")
        raise LlmNotConfiguredError(
            "Текстовое редактирование не настроено: задайте YANDEX_CLOUD_API_KEY и "
            "YANDEX_CLOUD_FOLDER (см. .env.example)."
        )


def _client_and_model() -> tuple[openai.AsyncOpenAI, str]:
    check_configured()
    if _llm_provider() == "gemini":
        client = openai.AsyncOpenAI(
            api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_BASE_URL, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES
        )
        return client, os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    api_key, folder = yandex_ai.credentials()
    model = yandex_ai.model_uri(folder, "YANDEX_CLOUD_MODEL", yandex_ai.DEFAULT_EDITOR_MODEL)
    return yandex_ai.make_client(api_key, folder, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES), model


def _extract_json(text: str) -> dict:
    """Модели нередко оборачивают JSON в ```json ... ``` или добавляют фразу
    перед ним, даже когда просили этого не делать. Берём от первой `{` до
    последней `}` -- этого достаточно для ответа с одним объектом верхнего
    уровня, которого мы и требуем."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LlmError(f"Модель не вернула JSON: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LlmError(f"Модель вернула некорректный JSON: {e}") from e


async def _call_responses(client: openai.AsyncOpenAI, model: str, user_input: str) -> tuple[str, object, object]:
    """Yandex Cloud -- OpenAI Responses API (client.responses.create)."""
    response = await client.responses.create(
        model=model,
        temperature=TEMPERATURE,
        instructions=INSTRUCTIONS,
        input=user_input,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        logger.warning("ответ оборван: %s", reason)
        if reason == "max_output_tokens":
            raise LlmError(
                "Модель не уложилась в лимит ответа -- рассуждала слишком долго. "
                "Попробуйте сформулировать просьбу проще или разбить на части."
            )
        raise LlmError(f"Модель не завершила ответ ({reason or 'причина неизвестна'})")
    usage = response.usage
    return response.output_text or "", getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?")


async def _call_chat_completions(client: openai.AsyncOpenAI, model: str, user_input: str) -> tuple[str, object, object]:
    """Gemini -- поддерживает только Chat Completions, не Responses API
    (см. https://ai.google.dev/gemini-api/docs/openai)."""
    response = await client.chat.completions.create(
        model=model,
        temperature=TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": user_input},
        ],
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        logger.warning("ответ оборван: превышен лимит токенов")
        raise LlmError(
            "Модель не уложилась в лимит ответа -- рассуждала слишком долго. "
            "Попробуйте сформулировать просьбу проще или разбить на части."
        )
    usage = response.usage
    return (
        choice.message.content or "",
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
    )


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _history_block(history: list[ChatTurn]) -> str:
    """Прошлые правки чата -- коротко, последние MAX_HISTORY_TURNS."""
    lines = []
    for number, turn in enumerate(history[-MAX_HISTORY_TURNS:], 1):
        lines.append(f"{number}. Просьба: {_clip(turn.instruction, MAX_HISTORY_TEXT)}")
        if turn.explanation:
            lines.append(f"   Ответ: {_clip(turn.explanation, MAX_HISTORY_TEXT)}")
        for applied in turn.applied[:MAX_HISTORY_APPLIED]:
            lines.append(f"   сделано: {_clip(applied, MAX_HISTORY_APPLIED_TEXT)}")
        if turn.added_ids:
            ids = turn.added_ids[:MAX_HISTORY_IDS]
            more = f" и ещё {len(turn.added_ids) - len(ids)}" if len(turn.added_ids) > len(ids) else ""
            lines.append(f"   новые объекты (id): {', '.join(ids)}{more}")
    return "\n".join(lines)


def build_user_input(
    scene: Scene,
    instruction: str,
    catalog: list[CatalogItem],
    placer: Placer,
    history: list[ChatTurn] = (),
) -> str:
    """Запрос к модели: контекст участка, прошлые правки и просьба. Считает
    геометрию -- вызывать из пула потоков."""
    context = _build_context(scene, catalog, placer, instruction)
    user_input = f"Контекст:\n{context}\n\n"
    if history:
        user_input += f"Прошлые правки в этом чате (уже применены):\n{_history_block(list(history))}\n\n"
    user_input += f"Просьба пользователя:\n{instruction}"
    logger.info("запрос: %r | контекст %d симв.", instruction[:200], len(context))
    return user_input


async def ask_model(user_input: str) -> LlmPlan:
    """План операций от модели по готовому запросу."""
    client, model = _client_and_model()
    provider = _llm_provider()
    started = time.monotonic()
    try:
        if provider == "gemini":
            text, input_tokens, output_tokens = await _call_chat_completions(client, model, user_input)
        else:
            text, input_tokens, output_tokens = await _call_responses(client, model, user_input)
    except openai.OpenAIError as e:
        logger.warning("LLM недоступна через %.1f с: %s", time.monotonic() - started, e)
        raise LlmError(f"LLM недоступна: {e}") from e
    finally:
        await yandex_ai.close_client(client)

    logger.info(
        "ответ (%s) за %.1f с | токены: вход %s, выход %s (лимит %d)",
        provider,
        time.monotonic() - started,
        input_tokens,
        output_tokens,
        MAX_OUTPUT_TOKENS,
    )

    try:
        return LlmPlan.model_validate(_extract_json(text))
    except LlmError:
        logger.warning("ответ не разобрать как JSON, начало: %r", text[:300])
        raise
    except ValidationError as e:
        logger.warning("план в неожиданном формате (%d ошибок), начало: %r", e.error_count(), text[:300])
        raise LlmError(f"Модель вернула план в неожиданном формате: {e.error_count()} ошибок") from e


async def request_plan(
    scene: Scene,
    instruction: str,
    catalog: list[CatalogItem],
    placer: Placer,
    history: list[ChatTurn] = (),
) -> LlmPlan:
    """build_user_input и ask_model одним вызовом -- для тестов и инструментов."""
    return await ask_model(build_user_input(scene, instruction, catalog, placer, history))
