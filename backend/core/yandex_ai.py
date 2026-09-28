"""
Клиент Yandex AI Studio -- OpenAI-совместимый API (openai-клиент с другим
base_url и каталогом в project). Общий для обеих LLM проекта:

* правка плана текстом (text_editor/llm_client.py) -- модель
  YANDEX_CLOUD_MODEL, по умолчанию Qwen3 235B: строгий JSON по длинной
  инструкции, без рассуждений, которые съедают лимит ответа;
* текст-обоснование решений GreenPlan (greenplan/decision_report.py) --
  модель YANDEX_CLOUD_REPORT_MODEL, по умолчанию YandexGPT 5.1 Pro: связный
  русский текст пояснительной записки.

Ключ и каталог одни на обе: YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER (см.
.env.example). Модель адресуется URI gpt://<каталог>/<модель>.
"""

from __future__ import annotations

import os
from typing import Optional

import openai
from dotenv import load_dotenv

from core.paths import ENV_FILE

# Локально .env лежит в корне репозитория. В Docker переменные уже приходят из
# env_file, а load_dotenv без override существующие значения не перетирает.
load_dotenv(ENV_FILE)

YANDEX_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
DEFAULT_EDITOR_MODEL = "qwen3-235b-a22b-fp8/latest"
DEFAULT_REPORT_MODEL = "yandexgpt-5.1/latest"


def credentials() -> Optional[tuple[str, str]]:
    """(ключ, каталог) или None, если чего-то из них нет."""
    api_key = os.environ.get("YANDEX_CLOUD_API_KEY", "").strip()
    folder = os.environ.get("YANDEX_CLOUD_FOLDER", "").strip()
    return (api_key, folder) if api_key and folder else None


def make_client(api_key: str, folder: str, **kwargs) -> openai.AsyncOpenAI:
    """Асинхронный клиент: запрос к модели не занимает поток, пока ждёт ответа."""
    return openai.AsyncOpenAI(api_key=api_key, base_url=YANDEX_BASE_URL, project=folder, **kwargs)


async def close_client(client) -> None:
    """Закрыть HTTP-соединения клиента после запроса."""
    close = getattr(client, "close", None)
    if close is not None:
        await close()


def model_uri(folder: str, env_var: str, default: str) -> str:
    """URI модели из переменной окружения env_var (или default)."""
    model = os.environ.get(env_var, "").strip() or default
    return f"gpt://{folder}/{model}"
