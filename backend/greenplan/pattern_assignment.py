"""
Назначение паттернов зонам -- GreenPlan, Этап 4 (issue #23, "Назначение
зон/паттернов"), правилами и без LLM. Опциональное ранжирование
отфильтрованного списка локальной LLM, которое issue #23 допускает для
неоднозначных случаев, здесь сознательно не реализовано -- локальная LLM
(Ollama) ещё не подключена нигде в проекте, это отдельная задача. Вместо неё
голос ближайших соседей retrieval-корпуса (pattern_retrieval.nearest_projects)
взвешенный их сходством и есть правило ранжирования: чем более похож
прошлый проект, тем больше у его выбора вес. Для каждого вида зоны голосуют
k самых похожих проектов, у которых есть решение для этого вида, а зоны
вида делятся между их паттернами пропорционально весу голосов (по площади).

Если НИ ОДИН похожий проект не использовал этот вид зоны ни разу
(zone.kind просто не встретился ни в одном похожем проекте) -- берётся
паттерн по умолчанию из pattern_library.DEFAULT_PATTERN_BY_ZONE_KIND, а не
молчаливая выдумка: у такого назначения confidence=0.0 и source_project=None,
и по этим полям видно, что это запасной вариант, а не результат retrieval.
Это ровно то поле, на которое ляжет Этап 6 (отчёт решений, "по аналогии с
проектом X" / без пометки -- по умолчанию), без переделки структуры.
"""

from __future__ import annotations

from pydantic import BaseModel

from core.schemas import Scene
from greenplan.pattern_corpus import CORPUS_SLUGS, PatternRecord, load_pattern_log
from greenplan.pattern_library import DEFAULT_PATTERN_BY_ZONE_KIND, PATTERN_LIBRARY
from greenplan.pattern_retrieval import NeighborMatch, nearest_projects
from greenplan.site_characterization import SiteCharacteristics, characterize_site
from greenplan.zone_partitioning import GeometricZone, ZoneKind


class ZoneAssignment(BaseModel):
    zone_id: str
    zone_kind: ZoneKind
    pattern_id: str
    source_project: str | None
    source_quote: str | None
    confidence: float
    # Площадь зоны, м² -- для документации (greenplan/document.py), чтобы не
    # пересчитывать разбиение на зоны ради одной цифры.
    zone_area_sqm: float = 0.0
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
        zone_area_sqm=zone.area_sqm,
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

    ranked = nearest_projects(characteristics, len(CORPUS_SLUGS))
    pattern_log = load_pattern_log()

    chosen: dict[str, ZoneAssignment] = {}
    for kind in dict.fromkeys(zone.kind for zone in zones):
        of_kind = [zone for zone in zones if zone.kind == kind]
        voters = _voters(kind, ranked, pattern_log, k)
        if not voters:
            for zone in of_kind:
                chosen[zone.id] = _fallback(zone)
            continue

        weight: dict[str, float] = {}
        for neighbor, record in voters:
            weight[record.pattern] = weight.get(record.pattern, 0.0) + neighbor.similarity
        total_weight = sum(weight.values())

        # Зоны раздаются между паттернами соседей по площади, пропорционально
        # весу голосов: крупнейшие зоны первыми, каждая -- паттерну, сильнее
        # всех недобравшему свою долю площади. Раньше все зоны вида получали
        # один победивший паттерн, и участок целиком заливался одним приёмом
        # (типичный двор -- сплошь волнами 10_stary_gay).
        assigned_area = dict.fromkeys(weight, 0.0)
        for zone in sorted(of_kind, key=lambda z: -z.area_sqm):
            area_after = sum(assigned_area.values()) + zone.area_sqm
            pattern = max(
                weight,
                key=lambda p: (weight[p] / total_weight * area_after - assigned_area[p], weight[p]),
            )
            assigned_area[pattern] += zone.area_sqm
            backers = [(n, r) for n, r in voters if r.pattern == pattern]
            best_neighbor, best_record = max(backers, key=lambda v: v[0].similarity)
            chosen[zone.id] = ZoneAssignment(
                zone_id=zone.id,
                zone_kind=zone.kind,
                zone_area_sqm=zone.area_sqm,
                pattern_id=pattern,
                source_project=best_neighbor.slug,
                source_quote=best_record.source_quote,
                confidence=len(backers) / len(voters),
            )
    return [chosen[zone.id] for zone in zones]


def _voters(
    kind: ZoneKind,
    ranked: list[NeighborMatch],
    pattern_log: dict[str, dict[ZoneKind, PatternRecord]],
    k: int,
) -> list[tuple[NeighborMatch, PatternRecord]]:
    """k самых похожих проектов, у которых есть решение для зоны этого вида.
    Раньше брались k ближайших вообще, и проект без записи для вида (например
    02_peschany_pereulok без open_area) просто занимал место соседа -- за
    open_area типичного двора голосовал один 10_stary_gay, и волны выигрывали
    без конкуренции. Проекты с неположительным сходством не голосуют: это не
    похожий участок, а противоположный."""
    voters: list[tuple[NeighborMatch, PatternRecord]] = []
    if k <= 0:
        return voters
    for neighbor in ranked:
        if neighbor.similarity <= 0:
            break
        record = pattern_log.get(neighbor.slug, {}).get(kind)
        # Защита от рассинхронизации data/pattern_corpus.yaml и
        # pattern_library.py: паттерн, семантически не подходящий этому виду
        # зоны, в голосовании не участвует.
        if record is None or kind not in PATTERN_LIBRARY[record.pattern].zone_kinds:
            continue
        voters.append((neighbor, record))
        if len(voters) == k:
            break
    return voters
