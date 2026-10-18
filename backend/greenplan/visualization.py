"""
Фотореалистичные визуализации по плану GreenPlan (бонусное задание ТЗ,
issue #114) -- Alice AI ART (бывший YandexART) в Yandex AI Studio; ключ и
каталог -- YANDEX_ART_* или те же, что у LLM проекта (core/yandex_ai.py).

Alice AI ART рисует по тексту, а не по чертежу: описание собирается из решений
GreenPlan (site_brief) -- стиль участка, приёмы по зонам, самые частые породы
с приметами внешнего вида (plant_appearance.py), газон, сохранённые деревья,
-- и картинка иллюстрирует замысел, а не точную расстановку. У каждого
ракурса свои акценты (prompt_for). Это прямо написано рядом с каждой картинкой («иллюстрация ИИ»):
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
from typing import Optional

import httpx

from core import yandex_ai
from core.schemas import Scene, is_offsite
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.plant_appearance import described, short_described

ART_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
OPERATION_URL = "https://llm.api.cloud.yandex.net:443/operations/{id}"
IMAGES_URL = f"{yandex_ai.YANDEX_BASE_URL}/images/generations"
# Широкий кадр: модель отдаёт 1344x768 (почти 16:9).
IMAGES_SIZE = "1792x1024"
# Длина запроса у Alice AI ART -- до 500 символов.
MAX_PROMPT_CHARS = 500
POLL_INTERVAL_S = 2.0
TIMEOUT_S = 120.0
NOTE = "Иллюстрация ИИ (Alice AI ART) по решениям GreenPlan — не проектный чертёж: точная расстановка — в DXF и записке."

# Ракурсы: что показать и как.
VIEWS: dict[str, str] = {
    "overview": "Вид с дрона, 40 м, под 45°, весь участок целиком",
    "pedestrian": "Вид с тротуара на уровне глаз, дорожка и посадки вдоль неё",
    "detail": "Крупный план посадок у дорожки",
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


# Приём -> как он выглядит на картинке.
PATTERN_LOOK: dict[str, str] = {
    "linear_hedge_row": "живые изгороди и ряды кустарника вдоль дорожек",
    "building_ring": "полосы кустарника вдоль фасадов",
    "diagonal_rows": "диагональные ряды деревьев",
    "flowing_rows": "плавные волнистые ряды посадок",
    "formal_bosque_grid": "боскет -- деревья по строгой сетке",
    "concentric_rings": "кольца посадок вокруг площадки",
    "triangular_grid_fill": "деревья в шахматном порядке",
    "grove_clusters": "свободные группы-рощицы деревьев",
    "poisson_scatter_fill": "одиночные деревья, свободно разбросанные по газону",
}
# Приёмы, которые видно с тротуара.
_STREET_PATTERNS = ("linear_hedge_row", "building_ring", "flowing_rows", "diagonal_rows")
_STYLE = {"regular": "регулярный стиль, строгая геометрия", "landscape": "пейзажный стиль, свободные группы"}
# Тип территории (двор, улица, площадь) в промпт не идёт: site_characterization
# угадывает его эвристикой, и неверное место действия портит картинку
# сильнее, чем нейтральное «городская территория».
FLOOR_HEIGHT_M = 3.0
TAIL = " Лето, ясный день, фотореализм, без текста."


@dataclass
class SiteBrief:
    """Что нарисовать: факты плана GreenPlan, из которых собираются промпты."""

    floors: Optional[int]
    has_buildings: bool
    style: Optional[str]
    patterns: list[str]  # id приёмов, частые первыми
    trees: list[tuple[str, Optional[str]]]  # (вид, catalogId), частые первыми
    bushes: list[tuple[str, Optional[str]]]
    existing_trees: int
    new_lawn: bool


def _species(objects) -> list[tuple[str, Optional[str]]]:
    counts = Counter((str(o.metadata.get("species")), o.metadata.get("catalogId")) for o in objects if o.metadata.get("species"))
    return [key for key, _count in counts.most_common()]


def site_brief(scene: Scene, assignments: list[ZoneAssignment]) -> SiteBrief:
    """Факты плана для промптов."""
    new = [o for o in scene.objects if o.metadata.get("generated")]
    buildings = [o for o in scene.objects if o.type == "building"]
    heights = sorted(h for o in buildings if isinstance(h := o.metadata.get("height"), (int, float)) and h > 0)
    floors = round(heights[len(heights) // 2] / FLOOR_HEIGHT_M) if heights else None
    patterns = Counter(a.pattern_id for a in assignments if a.pattern_id in PATTERN_LOOK)
    return SiteBrief(
        floors=floors if floors and 2 <= floors <= 40 else None,
        has_buildings=bool(buildings),
        style=next((_STYLE[a.site_style] for a in assignments if a.site_style in _STYLE), None),
        patterns=[pid for pid, _count in patterns.most_common()],
        trees=_species(o for o in new if o.type == "tree"),
        bushes=_species(o for o in new if o.type in ("bush", "hedge_segment")),
        existing_trees=sum(1 for o in scene.objects if o.type == "tree" and not o.metadata.get("generated") and not is_offsite(o)),
        new_lawn=any(lawn.status == "new" for lawn in scene.lawns),
    )


@dataclass
class _Part:
    group: str  # trees, bushes, patterns, extra
    full: str
    short: Optional[str]  # без примет -- если не помещается
    rank: int  # больше -- менее важно: укорачивается и выбрасывается первым


def _parts(view: str, brief: SiteBrief) -> list[_Part]:
    """Части промпта с важностью для ракурса: на общем виде важна планировка,
    с тротуара -- изгороди и ряды вдоль дорожек, на крупном плане -- породы."""
    parts: list[_Part] = []
    near = view == "detail"  # форма листьев -- только на крупном плане
    pattern_rank = {"overview": 2, "pedestrian": 2, "detail": 20}[view]
    patterns = brief.patterns
    if view == "pedestrian":
        patterns = [p for p in patterns if p in _STREET_PATTERNS] + [p for p in patterns if p not in _STREET_PATTERNS]
    for i, pid in enumerate(patterns[:3]):
        offset = 15 if view == "pedestrian" and pid not in _STREET_PATTERNS else 0
        parts.append(_Part("patterns", PATTERN_LOOK[pid], None, pattern_rank + 3 * i + offset))
    tree_ranks, bush_ranks = (1, 4, 9), (3, 7, 10)
    if view == "detail":
        tree_ranks, bush_ranks = (1, 3, 7), (2, 4, 6)
    for i, (species, catalog_id) in enumerate(brief.trees[:3]):
        parts.append(_Part("trees", described(species, catalog_id, near), short_described(species), tree_ranks[i]))
    for i, (species, catalog_id) in enumerate(brief.bushes[:3]):
        parts.append(_Part("bushes", described(species, catalog_id, near), short_described(species), bush_ranks[i]))
    if brief.new_lawn:
        parts.append(_Part("extra", "ухоженный газон", None, 5))
    if brief.existing_trees:
        parts.append(_Part("extra", "сохранённые взрослые деревья", None, 8))
    return parts


def _render(view: str, brief: SiteBrief, parts: list[_Part], shortened: set[int]) -> str:
    homes = f"жилой квартал, {brief.floors}-этажные дома" if brief.floors else "жилой квартал"
    context = "Москва, " + homes if brief.has_buildings else "Москва, городская территория"
    sentences = [VIEWS[view], context + (f", {brief.style}" if brief.style else "")]
    # «только эти породы» -- иначе модель дорисовывает берёзы и ели, которых в плане нет.
    for group, title in (("patterns", "Посадки"), ("trees", "Деревья только этих пород"), ("bushes", "Кустарники"), ("extra", "")):
        items = [(p.short if id(p) in shortened else p.full) for p in parts if p.group == group]
        if items:
            text = ", ".join(items)
            sentences.append(f"{title}: {text}" if title else text[0].upper() + text[1:])
    return ". ".join(sentences) + "." + TAIL


def prompt_for(view: str, brief: SiteBrief) -> str:
    """Промпт ракурса не длиннее MAX_PROMPT_CHARS. Не помещается -- у наименее
    важной части сначала убираются приметы вида, потом она сама."""
    parts = _parts(view, brief)
    shortened: set[int] = set()
    text = _render(view, brief, parts, shortened)
    while len(text) > MAX_PROMPT_CHARS and parts:
        worst = max(parts, key=lambda p: p.rank)
        if worst.short and id(worst) not in shortened:
            shortened.add(id(worst))
            worst.rank -= 0.5  # без примет вид ещё держится, пока не уйдут менее важные
        else:
            parts.remove(worst)
        text = _render(view, brief, parts, shortened)
    return text[:MAX_PROMPT_CHARS]


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
    brief = site_brief(scene, assignments)
    prompts = [(view, prompt_for(view, brief)) for view in views]
    async with httpx.AsyncClient(timeout=30.0) as client:
        images = await asyncio.gather(*(_generate(client, api_key, folder, prompt, seed + i, model) for i, (_view, prompt) in enumerate(prompts)))
    return [Visualization(view, prompt, image) for (view, prompt), image in zip(prompts, images, strict=True)]
