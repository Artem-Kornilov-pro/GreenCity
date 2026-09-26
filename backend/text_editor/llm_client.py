"""
Вызов LLM для правки текстом (text_editor/service.py): выбор провайдера по
LLM_PROVIDER ("gemini" по умолчанию или "yandex", оба через
OpenAI-совместимый API), запрос и разбор ответа в LlmPlan. Gemini
поддерживает только Chat Completions, yandex-путь использует Responses API --
поэтому два метода (_call_responses/_call_chat_completions) под одной
_client_and_model. Без ключей -- LlmNotConfiguredError (503 в main.py), а не
падение сервиса.
"""

from __future__ import annotations

import json
import logging
import os
import time

import openai
from dotenv import load_dotenv
from pydantic import ValidationError

from core.paths import ENV_FILE
from core.placement import (
    Placer,
)
from core.plant_catalog import CatalogItem
from core.schemas import Scene
from text_editor.operations import ChatTurn, LlmPlan
from text_editor.prompt import INSTRUCTIONS, _build_context

# Локально .env лежит в корне репозитория. В Docker переменные уже приходят из
# env_file, а load_dotenv без override существующие значения не перетирает.
load_dotenv(ENV_FILE)

# Без этого лога причину сбоя правки текстом было не узнать: в логе доступа
# uvicorn видна только строка "502 Bad Gateway".
logger = logging.getLogger("greencity.llm")

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
TEMPERATURE = 0.3
# С запасом под рассуждающие модели. Текущая yandexgpt не рассуждает и тратит на
# ответ ~150-250 токенов -- лимит её не замедляет. Но если переключить
# YANDEX_CLOUD_MODEL на рассуждающую (deepseek-v4-flash), рассуждение и ответ
# идут в один лимит: на 2000 ответ обрывался пустым (status=incomplete), на 8000
# обычно укладывался в ~3-4 тыс., но изредка не хватало и его. Поле
# reasoning_tokens у Yandex всегда 0 -- ориентироваться можно только на status.
MAX_OUTPUT_TOKENS = 8000

# Сколько прошлых правок чата уходит модели и насколько коротко: для
# отсылок ("убери их", "там же") хватает последних просьб и того, что по ним
# сделано, а каждый символ -- токены на каждом запросе.
MAX_HISTORY_TURNS = 4
MAX_HISTORY_TEXT = 300
MAX_HISTORY_APPLIED = 4
MAX_HISTORY_APPLIED_TEXT = 160


class LlmNotConfiguredError(RuntimeError):
    """Не заданы ключи LLM -- сервис работает, но текстовые правки недоступны."""


class LlmError(RuntimeError):
    """LLM недоступна или вернула ответ, который не удалось разобрать."""

# --- Вызов модели -----------------------------------------------------------


def _llm_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


def _client_and_model() -> tuple[openai.OpenAI, str]:
    if _llm_provider() == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        if not api_key:
            logger.warning("не задан GEMINI_API_KEY")
            raise LlmNotConfiguredError(
                "Текстовое редактирование не настроено: задайте GEMINI_API_KEY (см. .env.example)."
            )
        client = openai.OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)
        return client, model

    api_key = os.environ.get("YANDEX_CLOUD_API_KEY")
    folder = os.environ.get("YANDEX_CLOUD_FOLDER")
    model = os.environ.get("YANDEX_CLOUD_MODEL", "yandexgpt/latest")
    if not api_key or not folder:
        logger.warning("не заданы YANDEX_CLOUD_API_KEY / YANDEX_CLOUD_FOLDER")
        raise LlmNotConfiguredError(
            "Текстовое редактирование не настроено: задайте YANDEX_CLOUD_API_KEY и "
            "YANDEX_CLOUD_FOLDER (см. .env.example)."
        )
    client = openai.OpenAI(api_key=api_key, base_url=YANDEX_BASE_URL, project=folder)
    return client, f"gpt://{folder}/{model}"


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


def _call_responses(client: openai.OpenAI, model: str, user_input: str) -> tuple[str, object, object]:
    """Yandex Cloud -- OpenAI Responses API (client.responses.create)."""
    response = client.responses.create(
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


def _call_chat_completions(client: openai.OpenAI, model: str, user_input: str) -> tuple[str, object, object]:
    """Gemini -- поддерживает только Chat Completions, не Responses API
    (см. https://ai.google.dev/gemini-api/docs/openai)."""
    response = client.chat.completions.create(
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
    return "\n".join(lines)


def request_plan(
    scene: Scene,
    instruction: str,
    catalog: list[CatalogItem],
    placer: Placer,
    history: list[ChatTurn] = (),
) -> LlmPlan:
    client, model = _client_and_model()
    provider = _llm_provider()
    context = _build_context(scene, catalog, placer, instruction)
    user_input = f"Контекст:\n{context}\n\n"
    if history:
        user_input += f"Прошлые правки в этом чате (уже применены):\n{_history_block(list(history))}\n\n"
    user_input += f"Просьба пользователя:\n{instruction}"
    logger.info("запрос (%s): %r | контекст %d симв.", provider, instruction[:200], len(context))

    started = time.monotonic()
    try:
        if provider == "gemini":
            text, input_tokens, output_tokens = _call_chat_completions(client, model, user_input)
        else:
            text, input_tokens, output_tokens = _call_responses(client, model, user_input)
    except openai.OpenAIError as e:
        logger.warning("LLM недоступна через %.1f с: %s", time.monotonic() - started, e)
        raise LlmError(f"LLM недоступна: {e}") from e

    logger.info(
        "ответ за %.1f с | токены: вход %s, выход %s (лимит %d)",
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
