"""
Фотореалистичные визуализации по плану GreenPlan (бонусное задание ТЗ,
issue #114) -- Alice AI ART (бывший YandexART) в Yandex AI Studio; ключ и
каталог -- YANDEX_ART_* или те же, что у LLM проекта (core/yandex_ai.py).

Alice AI ART рисует по тексту, а не по чертежу: описание собирается из решений
GreenPlan -- стиль участка, приёмы по зонам, породы деревьев и кустарников,
сколько их, есть ли газон, -- и картинка иллюстрирует замысел, а не точную
расстановку. Это прямо написано рядом с каждой картинкой («иллюстрация ИИ»):
проектный документ -- DXF и записка.

Модель по умолчанию -- aliceai-image-art-3.0 через OpenAI-совместимый
/v1/images/generations (картинка сразу в ответе). Старый асинхронный API
(операция и опрос) -- только для yandex-art/latest, который Yandex вывел из
эксплуатации: отвечает «Access to model ... denied».
"""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from dataclasses import dataclass

import httpx

from core import yandex_ai
from core.schemas import Scene
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pattern_library import PATTERN_LIBRARY

ART_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
OPERATION_URL = "https://llm.api.cloud.yandex.net:443/operations/{id}"
IMAGES_URL = f"{yandex_ai.YANDEX_BASE_URL}/images/generations"
IMAGES_SIZE = "1024x1024"
# Длина запроса у Alice AI ART -- до 500 символов.
MAX_PROMPT_CHARS = 500
POLL_INTERVAL_S = 2.0
TIMEOUT_S = 120.0
NOTE = "Иллюстрация ИИ (Alice AI ART) по решениям GreenPlan — не проектный чертёж: точная расстановка — в DXF и записке."

# Ракурсы: что показать и как.
VIEWS: dict[str, str] = {
    "overview": "Общий вид сверху под углом 45 градусов",
    "pedestrian": "Вид на уровне глаз пешехода с тротуара",
    "detail": "Крупный план посадок: деревья, кустарники и газон",
}


class VisualizationNotConfigured(RuntimeError):
    """Нет ключа или каталога Yandex AI Studio."""


class VisualizationFailed(RuntimeError):
    """Alice AI ART не вернул изображение -- текст для пользователя."""


@dataclass
class Visualization:
    view: str
    prompt: str
    image_jpeg: bytes


def _top(names, n: int) -> list[str]:
    return [name for name, _count in Counter(names).most_common(n)]


# «озеленение в пейзажном стиле» -- STYLE_LABELS в предложном падеже.
_STYLE_IN = {"regular": "регулярном", "landscape": "пейзажном"}


def _short(label: str) -> str:
    """Название приёма без пояснения: «органичные рощи -- случайные группы...» -> «органичные рощи»."""
    return label.split(" -- ")[0].split(" (")[0].strip().lower()


def plan_description(scene: Scene, assignments: list[ZoneAssignment]) -> str:
    """Замысел плана одним абзацем -- для промпта Alice AI ART."""
    new = [o for o in scene.objects if o.metadata.get("generated")]
    trees = _top((str(o.metadata.get("species") or "") for o in new if o.type == "tree"), 3)
    bushes = _top((str(o.metadata.get("species") or "") for o in new if o.type == "bush"), 3)
    patterns = _top((_short(PATTERN_LIBRARY[a.pattern_id].label) for a in assignments if a.pattern_id in PATTERN_LIBRARY), 3)
    style = next((_STYLE_IN.get(a.site_style or "") for a in assignments if a.site_style), None)
    buildings = sum(1 for o in scene.objects if o.type == "building")
    parts = ["Городской участок в Москве"]
    if buildings:
        parts.append("между многоэтажными жилыми домами")
    if style:
        parts.append(f"озеленение в {style} стиле")
    if patterns:
        parts.append("приёмы: " + ", ".join(patterns))
    if trees:
        parts.append("деревья: " + ", ".join(t.lower() for t in trees if t))
    if bushes:
        parts.append("кустарники: " + ", ".join(b.lower() for b in bushes if b))
    if any(lawn.status == "new" for lawn in scene.lawns):
        parts.append("ухоженный газон")
    return "; ".join(p for p in parts if p) + "."


def prompt_for(view: str, description: str) -> str:
    """Промпт не длиннее MAX_PROMPT_CHARS: лишнее описание отбрасывается с
    конца, по целым частям (приёмы, породы), ракурс и стиль картинки остаются."""
    head = f"{VIEWS[view]}. "
    tail = " Лето, ясный день. Фотореалистичная визуализация благоустройства, без текста и надписей."
    parts = description.rstrip(".").split("; ")
    while len(parts) > 1 and len(head) + len("; ".join(parts)) + 1 + len(tail) > MAX_PROMPT_CHARS:
        parts.pop()
    return (head + "; ".join(parts) + "." + tail)[:MAX_PROMPT_CHARS]


def _access_denied() -> VisualizationFailed:
    return VisualizationFailed(
        "нет доступа к Alice AI ART: выдайте сервисному аккаунту ключа (YANDEX_ART_API_KEY или YANDEX_CLOUD_API_KEY) роль "
        "ai.imageGeneration.user в его каталоге (YANDEX_ART_FOLDER или YANDEX_CLOUD_FOLDER); у ключа должна быть "
        "область yc.ai.imageGeneration.execute"
    )


def _openai_api(model: str) -> bool:
    """Все модели, кроме старого yandex-art/... -- через OpenAI-совместимый API."""
    return not model.startswith("yandex-art/")


async def _generate(
    client: httpx.AsyncClient, api_key: str, folder: str, prompt: str, seed: int, model: str = yandex_ai.DEFAULT_ART_MODEL
) -> bytes:
    if _openai_api(model):
        return await _generate_openai(client, api_key, folder, prompt, model)
    headers = {"Authorization": f"Api-Key {api_key}"}
    body = {
        "modelUri": f"art://{folder}/{model}",
        "generationOptions": {"seed": str(seed), "aspectRatio": {"widthRatio": "16", "heightRatio": "9"}},
        "messages": [{"weight": "1", "text": prompt}],
    }
    started = await client.post(ART_URL, json=body, headers=headers)
    if started.status_code in (401, 403):
        raise _access_denied()
    if started.status_code != 200:
        raise VisualizationFailed(f"Alice AI ART отказал ({started.status_code}): {started.text[:200]}")
    operation_id = started.json().get("id")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TIMEOUT_S
    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        status = await client.get(OPERATION_URL.format(id=operation_id), headers=headers)
        data = status.json() if status.status_code == 200 else {}
        if data.get("done"):
            if "error" in data:
                raise VisualizationFailed(f"Alice AI ART: {data['error'].get('message', data['error'])}")
            return base64.b64decode(data["response"]["image"])
    raise VisualizationFailed(f"Alice AI ART не ответил за {int(TIMEOUT_S)} с")


async def _generate_openai(client: httpx.AsyncClient, api_key: str, folder: str, prompt: str, model: str) -> bytes:
    headers = {"Authorization": f"Bearer {api_key}", "OpenAI-Project": folder}
    body = {"model": f"art://{folder}/{model}", "prompt": prompt, "size": IMAGES_SIZE}
    res = await client.post(IMAGES_URL, json=body, headers=headers, timeout=TIMEOUT_S)
    if res.status_code in (401, 403):
        raise _access_denied()
    if res.status_code != 200:
        # Модель, к которой у ключа нет доступа, этот API отдаёт как 500
        # «failed to execute calls to all endpoints».
        hint = " -- обычно это значит, что у ключа нет доступа к модели" if "all endpoints" in res.text else ""
        raise VisualizationFailed(f"Alice AI ART отказал ({res.status_code}): {res.text[:200]}{hint}")
    try:
        return base64.b64decode(res.json()["data"][0]["b64_json"])
    except (KeyError, IndexError, ValueError) as e:
        raise VisualizationFailed(f"Alice AI ART вернул ответ без изображения: {res.text[:200]}") from e


async def generate_visualizations(
    scene: Scene, assignments: list[ZoneAssignment], views: list[str], seed: int = 1
) -> list[Visualization]:
    """Картинки для ракурсов views (ключи VIEWS) -- параллельно."""
    creds = yandex_ai.art_credentials()
    if creds is None:
        raise VisualizationNotConfigured(
            "Визуализации не настроены: задайте YANDEX_CLOUD_API_KEY и YANDEX_CLOUD_FOLDER или YANDEX_ART_API_KEY и YANDEX_ART_FOLDER (см. .env.example)."
        )
    api_key, folder, model = creds
    description = plan_description(scene, assignments)
    prompts = [(view, prompt_for(view, description)) for view in views]
    async with httpx.AsyncClient(timeout=30.0) as client:
        images = await asyncio.gather(*(_generate(client, api_key, folder, prompt, seed + i, model) for i, (_view, prompt) in enumerate(prompts)))
    return [Visualization(view, prompt, image) for (view, prompt), image in zip(prompts, images, strict=True)]
