"""
Назначение паттернов зонам -- GreenPlan, Этап 4 (issue #23, "Назначение
зон/паттернов"), правилами и без LLM. Опциональное ранжирование
отфильтрованного списка локальной LLM, которое issue #23 допускает для
неоднозначных случаев, здесь сознательно не реализовано -- локальная LLM
(Ollama) ещё не подключена нигде в проекте, это отдельная задача. Вместо неё
голос ближайших соседей retrieval-корпуса (pattern_retrieval.nearest_projects)
взвешенный их сходством и есть правило ранжирования: чем более похож
прошлый проект, тем больше у его выбора вес.

Если НИ ОДИН из k ближайших соседей не использовал этот вид зоны ни разу
(zone.kind просто не встретился ни в одном похожем проекте) -- берётся
паттерн по умолчанию из pattern_library.DEFAULT_PATTERN_BY_ZONE_KIND, а не
молчаливая выдумка: у такого назначения confidence=0.0 и source_project=None,
и по этим полям видно, что это запасной вариант, а не результат retrieval.
Это ровно то поле, на которое ляжет Этап 6 (отчёт решений, "по аналогии с
проектом X" / без пометки -- по умолчанию), без переделки структуры.
"""

from __future__ import annotations

from pattern_corpus import load_pattern_log
from pattern_library import DEFAULT_PATTERN_BY_ZONE_KIND, PATTERN_LIBRARY
from pattern_retrieval import nearest_projects
from pydantic import BaseModel
from schemas import Scene
from site_characterization import SiteCharacteristics, characterize_site
from zone_partitioning import GeometricZone, ZoneKind


class ZoneAssignment(BaseModel):
    zone_id: str
    zone_kind: ZoneKind
    pattern_id: str
    source_project: str | None
    source_quote: str | None
    confidence: float
    # Виды растений, подобранные для этой зоны (species_selection.py), и
    # основание подбора -- заполняет deterministic_placement.generate_for_scene
    # после расстановки; assign_patterns их не знает.
    tree_species: list[str] = []
    bush_species: list[str] = []
    species_basis: str | None = None


def _fallback(zone: GeometricZone) -> ZoneAssignment:
    return ZoneAssignment(
        zone_id=zone.id,
        zone_kind=zone.kind,
        pattern_id=DEFAULT_PATTERN_BY_ZONE_KIND[zone.kind],
        source_project=None,
        source_quote=None,
        confidence=0.0,
    )


def assign_patterns(
    scene: Scene,
    zones: list[GeometricZone],
    k: int = 3,
    characteristics: SiteCharacteristics | None = None,
) -> list[ZoneAssignment]:
    """characteristics -- проброс уже посчитанного characterize_site(scene)
    вызывающим кодом (deterministic_placement.generate_for_scene), который
    и так вызывает его сам ради usable_planting_area: без этого параметра
    один и тот же дорогой unary_union по зданиям/зонам участка считался бы
    внутри одного запроса дважды. Указан явно (не просто дефолт None) --
    вызовы напрямую (тесты, pattern_corpus.py) продолжают считать сами."""
    if not zones:
        return []

    if characteristics is None:
        characteristics = characterize_site(scene)
    if characteristics is None:
        # Нет валидной границы участка -- retrieval считать не от чего же,
        # что и partition_zones уже проверил (иначе зон бы не было), но
        # defensive: без границы честный ответ -- запасной паттерн для всех.
        return [_fallback(zone) for zone in zones]

    neighbors = nearest_projects(characteristics, k)
    pattern_log = load_pattern_log()

    assignments: list[ZoneAssignment] = []
    for zone in zones:
        votes: list[tuple[str, float, str, str]] = []  # (pattern_id, similarity, slug, quote)
        for neighbor in neighbors:
            record = pattern_log.get(neighbor.slug, {}).get(zone.kind)
            if record is None:
                continue
            # Защита от рассинхронизации data/pattern_corpus.yaml и
            # pattern_library.py: паттерн, семантически не подходящий этому
            # виду зоны, в голосовании не участвует, даже если в корпусе
            # вдруг оказалась такая запись.
            if zone.kind not in PATTERN_LIBRARY[record.pattern].zone_kinds:
                continue
            votes.append((record.pattern, neighbor.similarity, neighbor.slug, record.source_quote))

        if not votes:
            assignments.append(_fallback(zone))
            continue

        weight_by_pattern: dict[str, float] = {}
        for pattern_id, similarity, _, _ in votes:
            weight_by_pattern[pattern_id] = weight_by_pattern.get(pattern_id, 0.0) + similarity
        winner = max(weight_by_pattern, key=lambda p: weight_by_pattern[p])

        winner_votes = [v for v in votes if v[0] == winner]
        # Источник для отчёта -- сосед, отдавший голос за победивший паттерн
        # и наиболее похожий на новый участок среди них.
        best = max(winner_votes, key=lambda v: v[1])
        assignments.append(
            ZoneAssignment(
                zone_id=zone.id,
                zone_kind=zone.kind,
                pattern_id=winner,
                source_project=best[2],
                source_quote=best[3],
                confidence=len(winner_votes) / len(neighbors),
            )
        )
    return assignments
