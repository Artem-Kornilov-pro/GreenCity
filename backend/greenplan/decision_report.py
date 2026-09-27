"""
Текст-обоснование решений GreenPlan для заказчика: почему участок озеленён
именно так. Пишет YandexGPT 5.1 Pro (core/yandex_ai.py).

Решения (приём, проект-аналог, уверенность) уже приняты без LLM
(pattern_assignment.ZoneAssignment); модель только пересказывает их. Ей
запрещено добавлять виды, площади, номера зон и проекты, которых нет в
сводке.

Зоны сначала сводятся по (вид зоны, приём, проект-аналог): на участке бывают
сотни зон, и текст пишется не «зона 142 -> приём X», а «вдоль дорожек --
живая изгородь, как в проекте Y».

Без ключа или при недоступной модели поднимается DecisionReportUnavailable,
чтобы «отчёт недоступен» не путался с «нечего описывать».

Настройки: YANDEX_CLOUD_API_KEY, YANDEX_CLOUD_FOLDER, YANDEX_CLOUD_REPORT_MODEL
(по умолчанию yandexgpt-5.1/latest); читаются при каждом вызове.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Optional

import openai
from pydantic import BaseModel

from core import yandex_ai
from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pattern_library import PATTERN_LIBRARY, STYLE_LABELS
from greenplan.zone_partitioning import ZoneKind

# Низкая, но не нулевая: при 0 модель иногда пишет рубленые перечисления
# вместо связного текста.
TEMPERATURE = 0.2
# Несколько абзацев по-русски -- ~600-1000 токенов; с запасом, чтобы текст
# не обрывался на полуслове.
MAX_OUTPUT_TOKENS = 1500
# С запасом под облачный API в час пик: обычно ответ -- секунды.
REQUEST_TIMEOUT_S = 60.0
# Один повтор -- на сетевой сбой облачного API. Больше не нужно: каждый
# повтор -- ещё до REQUEST_TIMEOUT_S ожидания для пользователя.
MAX_RETRIES = 1

ZONE_KIND_LABELS: dict[ZoneKind, str] = {
    "building_border": "полоса вдоль зданий",
    "path_corridor": "полоса вдоль дорожек и проезжей части",
    "site_edge": "край участка",
    "open_area": "открытая площадь",
}

INSTRUCTIONS = """Ты помогаешь оформить отчёт по проекту озеленения городского участка.
Тебе дано общее решение на участок (стиль озеленения и ведущий проект-аналог)
и список УЖЕ ПРИНЯТЫХ решений по местам: для каждого вида места на участке указано,
какой приём озеленения выбран, на скольких зонах он применён, (если есть)
с какого похожего прошлого проекта этот приём взят и почему, какие виды
деревьев и кустарников подобраны и на каком основании.

Правила:
- Используй ТОЛЬКО перечисленные факты. Не придумывай виды растений, площади,
  названия проектов, зон или причины, которых нет в списке. Виды растений
  называй ровно так, как они написаны в списке.
- Если для решения не указан проект-источник -- это запасной вариант (типовое
  решение), а не заимствование; не приписывай ему проект.
- Пиши связный текст на русском языке, несколько абзацев, как итоговое
  объяснение для заказчика -- не список и не таблица. Начни с общего решения
  (стиль участка), затем объясни решения по местам как части этого замысла.
- Не упоминай, что ты языковая модель, и не добавляй ничего от себя вне
  пересказа данных фактов."""


class SummaryRow(BaseModel):
    zone_kind: ZoneKind
    pattern_id: str
    source_project: Optional[str]
    zone_count: int
    source_quote: Optional[str]
    confidence: float
    tree_species: list[str] = []
    bush_species: list[str] = []
    species_basis: Optional[str] = None


class DecisionReportUnavailable(RuntimeError):
    """LLM недоступна (сервер не поднят, таймаут, пустой/битый ответ) --
    отчёт в этом случае не существует вовсе, а не тихо остаётся пустым."""


def _model() -> str:
    folder = os.environ.get("YANDEX_CLOUD_FOLDER", "").strip()
    return yandex_ai.model_uri(folder, "YANDEX_CLOUD_REPORT_MODEL", yandex_ai.DEFAULT_REPORT_MODEL)


def _client() -> openai.OpenAI:
    creds = yandex_ai.credentials()
    if creds is None:
        raise DecisionReportUnavailable(
            "Текст-обоснование не настроено: задайте YANDEX_CLOUD_API_KEY и YANDEX_CLOUD_FOLDER (см. .env.example)."
        )
    # max_retries -- явно, вместо дефолта openai-SDK (2 повтора): каждый
    # повтор с тем же REQUEST_TIMEOUT_S множит время до ответа.
    return yandex_ai.make_client(*creds, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)


def _summarize(assignments: list[ZoneAssignment]) -> list[SummaryRow]:
    """Схлопывает список решений по зонам в сводку по (вид зоны, паттерн,
    проект-источник) -- источник данных для промпта. Внутри одной группы
    confidence может отличаться по зонам (разные соседи выиграли голосование
    в разных зонах того же вида) -- берём наибольшую, как самую уверенную
    иллюстрацию этого решения."""
    groups: dict[tuple[str, str, Optional[str]], list[ZoneAssignment]] = defaultdict(list)
    for assignment in assignments:
        key = (assignment.zone_kind, assignment.pattern_id, assignment.source_project)
        groups[key].append(assignment)

    rows = []
    for (zone_kind, pattern_id, source_project), members in groups.items():
        best = max(members, key=lambda a: a.confidence)
        rows.append(
            SummaryRow(
                zone_kind=zone_kind,
                pattern_id=pattern_id,
                source_project=source_project,
                zone_count=len(members),
                source_quote=best.source_quote,
                confidence=best.confidence,
                # Виды подбираются на пару (вид зоны, паттерн) на весь
                # участок (species_selection.py) -- у всех зон группы они одни.
                tree_species=best.tree_species,
                bush_species=best.bush_species,
                species_basis=best.species_basis,
            )
        )
    rows.sort(key=lambda r: r.zone_count, reverse=True)
    return rows


def _site_fact(assignment: ZoneAssignment) -> str:
    if assignment.site_style is None:
        return "Общее решение: стиль участка не определён (у похожих проектов нет решений определённого стиля)."
    line = f"Общее решение: стиль участка -- {STYLE_LABELS[assignment.site_style]}"
    if assignment.lead_project:
        line += f", ведущий проект-аналог -- {assignment.lead_project}"
    return line + "; приёмы по местам подобраны в этом стиле."


def _format_facts(rows: list[SummaryRow]) -> str:
    lines = []
    for row in rows:
        zone_label = ZONE_KIND_LABELS.get(row.zone_kind, row.zone_kind)
        pattern_label = PATTERN_LIBRARY[row.pattern_id].label
        line = f"- {zone_label} ({row.zone_count} зон{'а' if row.zone_count == 1 else ''}): {pattern_label}"
        if row.source_project:
            line += f" -- по аналогии с проектом {row.source_project}"
            if row.source_quote:
                # Цитаты в корпусе сами бывают в кавычках -- без strip
                # кавычки удвоились бы.
                quote = row.source_quote.strip().strip('"')
                line += f' ("{quote}")'
        else:
            line += " -- запасной вариант, без прямого прошлого проекта-образца"
        species = []
        if row.tree_species:
            species.append("деревья: " + ", ".join(row.tree_species))
        if row.bush_species:
            species.append("кустарники: " + ", ".join(row.bush_species))
        if species:
            line += "; " + "; ".join(species)
            if row.species_basis:
                line += f" (виды подобраны: {row.species_basis})"
        lines.append(line)
    return "\n".join(lines)


def generate_report(assignments: list[ZoneAssignment]) -> Optional[str]:
    """Связный текст-объяснение по списку решений или None, если описывать
    нечего (пустой список зон -- легитимный случай). Если LLM недоступна --
    DecisionReportUnavailable, а не пустой результат (см. докстринг модуля)."""
    if not assignments:
        return None

    facts = _site_fact(assignments[0]) + "\n" + _format_facts(_summarize(assignments))
    try:
        # Без ключа _client() сам поднимает DecisionReportUnavailable.
        response = _client().responses.create(
            model=_model(),
            temperature=TEMPERATURE,
            instructions=INSTRUCTIONS,
            input=f"Принятые решения по участку:\n{facts}",
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.OpenAIError as e:
        raise DecisionReportUnavailable(f"Не удалось получить отчёт от LLM: {e}") from e

    if response.status == "incomplete":
        # Оборванный на полуслове текст в записку заказчику не годится.
        reason = getattr(response.incomplete_details, "reason", None) or "причина неизвестна"
        raise DecisionReportUnavailable(f"LLM не завершила ответ ({reason})")
    text = (response.output_text or "").strip()
    if not text:
        raise DecisionReportUnavailable("LLM вернула пустой ответ")
    return text
