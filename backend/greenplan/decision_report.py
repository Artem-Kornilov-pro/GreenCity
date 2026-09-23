"""
Отчёт о решениях -- GreenPlan, Этап 6 (issue #23, "Отчёт о решениях... по
аналогии с проектом X"). Требование заказчика: сам текст объяснения, почему
озеленение участка сделано именно так, а не иначе, пишет LLM (mistral:7b
через локальный Ollama), а не шаблон/f-строка.

Решения (какой паттерн, с какого проекта он взят, с какой уверенностью) уже
посчитаны детерминированно и без LLM на Этапах 3-5
(pattern_assignment.ZoneAssignment) -- это не меняется. LLM здесь --
последний шаг: пересказать уже готовый список решений человеческим языком.
Тот же принцип, что и everywhere в GreenPlan ("LLM не считает и не
придумывает"), просто применённый не к координатам, а к фактам отчёта:
модели явно запрещено вводить виды растений, площади, номера зон или
проекты, которых нет в переданной сводке.

Один связный текст на весь участок, не фраза на каждую зону -- реальные
участки легко дают 50-200+ геометрических зон (zone_partitioning.py), и
отдельная LLM-фраза на каждую была бы и нечитаемой простынёй, и неподъёмным
промптом для 7B-модели. Поэтому сначала зоны АГРЕГИРУЮТСЯ по (вид зоны,
паттерн, проект-источник) -- ровно как прошлая сессия вручную писала
design_rationale.md: не "зона №142 -> паттерн X", а "вдоль дорожек по всему
участку -- живая изгородь, как на проекте Y".

В отличие от необязательных фич (где недоступность LLM тихо откатывается на
прежнее поведение), здесь LLM -- единственный способ получить текст: без
Ollama функция не возвращает пустоту молча, а поднимает
DecisionReportUnavailable, чтобы вызывающий код не перепутал "нечего
описывать" (пустой список решений) с "отчёт недоступен" (LLM не отвечает).

Независим от backend/text_editor/service.py (свой openai-клиент на своём base_url) --
по договорённости старый текстовый редактор (Gemini/Yandex) этот модуль не
трогает и от него не зависит.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Optional

import openai
from pydantic import BaseModel

from greenplan.pattern_assignment import ZoneAssignment
from greenplan.pattern_library import PATTERN_LIBRARY
from greenplan.zone_partitioning import ZoneKind

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral:7b")

# Низкая, но не нулевая -- отчёту заказчику нужна стабильная формулировка
# больше, чем творческое разнообразие, но совсем 0 иногда даёт рубленые
# перечисления вместо связного текста даже у моделей покрупнее.
TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 900
# Замерено напрямую на Ollama/mistral:7b (обычная машина разработчика, не
# GPU-сервер): ~26 токенов/с на прогретой модели -- полный ответ в
# MAX_OUTPUT_TOKENS у модели укладывается впритык или чуть за 30 секунд.
# Старые 30.0 с реально ловили таймаут при штатной (не сбойной) работе --
# найдено ручным тестированием ("мистраль не отвечает"), хотя сама модель и
# подключение были исправны. 60 -- запас почти вдвое от замеренного худшего
# случая, а не догадка.
REQUEST_TIMEOUT_S = 60.0

ZONE_KIND_LABELS: dict[ZoneKind, str] = {
    "building_border": "полоса вдоль зданий",
    "path_corridor": "полоса вдоль дорожек и проезжей части",
    "site_edge": "край участка",
    "open_area": "открытая площадь",
}

INSTRUCTIONS = """Ты помогаешь оформить отчёт по проекту озеленения городского участка.
Тебе дан список УЖЕ ПРИНЯТЫХ решений: для каждого вида места на участке указано,
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
  объяснение для заказчика -- не список и не таблица.
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


def _client() -> openai.OpenAI:
    # max_retries=0 -- явно, вместо дефолта openai-SDK (2 повтора). Для
    # облачного API повтор на сетевой сбой оправдан; для локальной модели,
    # которая просто медленно отвечает или недоступна, повтор с тем же
    # REQUEST_TIMEOUT_S на попытку МНОЖИТ время до ответа (до 3x), а не
    # спасает запрос -- Ollama не станет отвечать быстрее со второй попытки.
    # Это и превращало единичный подвисший вызов в 60-90-секундный, из-за
    # которого весь процесс (один воркер uvicorn, общий GIL) выглядел
    # "зависшим" и для остальных запросов, включая healthcheck.
    return openai.OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama", timeout=REQUEST_TIMEOUT_S, max_retries=0)


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


def _format_facts(rows: list[SummaryRow]) -> str:
    lines = []
    for row in rows:
        zone_label = ZONE_KIND_LABELS.get(row.zone_kind, row.zone_kind)
        pattern_label = PATTERN_LIBRARY[row.pattern_id].label
        line = f"- {zone_label} ({row.zone_count} зон{'а' if row.zone_count == 1 else ''}): {pattern_label}"
        if row.source_project:
            line += f" -- по аналогии с проектом {row.source_project}"
            if row.source_quote:
                # Цитаты в data/pattern_corpus.yaml часто сами начинаются/
                # заканчиваются кавычкой (дословный кусок design_rationale.md) --
                # без strip('"') вокруг них получалась бы двойная кавычка.
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

    facts = _format_facts(_summarize(assignments))
    try:
        response = _client().chat.completions.create(
            model=OLLAMA_MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": f"Принятые решения по участку:\n{facts}"},
            ],
        )
    except openai.OpenAIError as e:
        raise DecisionReportUnavailable(f"Не удалось получить отчёт от LLM: {e}") from e

    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise DecisionReportUnavailable("LLM вернула пустой ответ")
    return text
