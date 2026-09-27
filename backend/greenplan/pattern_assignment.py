"""
Назначение приёмов озеленения зонам -- правилами, без LLM. Решение в два
уровня, чтобы у участка был единый стиль:

1. Стиль участка: k самых похожих проектов, у которых для видов зон этого
   участка есть решение регулярного или пейзажного стиля, голосуют с весом,
   равным сходству. Ведущий проект -- самый похожий проект победившего стиля.
2. Приёмы зон: для каждого вида зоны голосуют k самых похожих проектов с
   решением в стиле участка (или нейтральным -- изгородь, кольцо у здания);
   зоны вида делятся между их приёмами пропорционально весу голосов.

Если аналога нет (сходство ниже MIN_VOTE_SIMILARITY), зона получает типовой
приём стиля с confidence=0 и source_project=None -- в записке он помечен
«проверить».
"""

from __future__ import annotations

from pydantic import BaseModel

from core.schemas import Scene
from greenplan.pattern_corpus import CORPUS_SLUGS, PatternRecord, load_pattern_log
from greenplan.pattern_library import PATTERN_LIBRARY, default_pattern
from greenplan.pattern_retrieval import NeighborMatch, nearest_projects
from greenplan.site_characterization import SiteCharacteristics, characterize_site
from greenplan.zone_partitioning import GeometricZone, ZoneKind

# Ниже этого сходства проект -- не аналог. Порог 0.2: доля площади зон на
# типовом приёме 5,6 %, и ни один тестовый участок не остаётся без аналогов.
MIN_VOTE_SIMILARITY = 0.2


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
    # Виды растений и основание подбора -- заполняет расстановка.
    tree_species: list[str] = []
    bush_species: list[str] = []
    species_basis: str | None = None
    # Общее решение на участок: стиль (None -- не определён) и ведущий аналог.
    site_style: str | None = None
    lead_project: str | None = None


def _fallback(zone: GeometricZone, site_style: str | None = None, lead_project: str | None = None) -> ZoneAssignment:
    return ZoneAssignment(
        zone_id=zone.id,
        zone_kind=zone.kind,
        zone_area_sqm=zone.area_sqm,
        pattern_id=default_pattern(zone.kind, site_style),
        source_project=None,
        source_quote=None,
        confidence=0.0,
        site_style=site_style,
        lead_project=lead_project,
    )


def assign_patterns(
    scene: Scene,
    zones: list[GeometricZone],
    k: int = 3,
    characteristics: SiteCharacteristics | None = None,
    style: str = "auto",
) -> list[ZoneAssignment]:
    """characteristics -- уже посчитанные признаки участка, чтобы не считать
    их дважды; None -- посчитать здесь."""
    if not zones:
        return []

    if characteristics is None:
        characteristics = characterize_site(scene)
    if characteristics is None:
        # Без границы участка аналоги искать не от чего.
        return [_fallback(zone) for zone in zones]

    ranked = nearest_projects(characteristics, len(CORPUS_SLUGS))
    pattern_log = load_pattern_log()
    kinds = list(dict.fromkeys(zone.kind for zone in zones))
    if style in ("regular", "landscape"):
        # Стиль задал пользователь (параметры GreenPlan) -- голосование за
        # стиль не нужно, ведущий аналог -- самый похожий проект этого стиля.
        site_style, lead_project = style, _lead_of_style(style, set(kinds), ranked, pattern_log)
    else:
        site_style, lead_project = _site_style(set(kinds), ranked, pattern_log, k)

    chosen: dict[str, ZoneAssignment] = {}
    for kind in kinds:
        of_kind = [zone for zone in zones if zone.kind == kind]
        voters = _voters(kind, ranked, pattern_log, k, site_style)
        if not voters:
            for zone in of_kind:
                chosen[zone.id] = _fallback(zone, site_style, lead_project)
            continue

        weight: dict[str, float] = {}
        for neighbor, record in voters:
            weight[record.pattern] = weight.get(record.pattern, 0.0) + neighbor.similarity
        total_weight = sum(weight.values())

        # Зоны делятся между приёмами по площади пропорционально весу голосов:
        # крупные первыми, каждая -- приёму, сильнее всех недобравшему долю.
        # Иначе один победитель заливал бы весь участок одним приёмом.
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
                site_style=site_style,
                lead_project=lead_project,
            )
    return [chosen[zone.id] for zone in zones]


def _site_style(
    kinds: set[ZoneKind],
    ranked: list[NeighborMatch],
    pattern_log: dict[str, dict[ZoneKind, PatternRecord]],
    k: int,
) -> tuple[str | None, str | None]:
    """(стиль участка, ведущий проект) -- голос k самых похожих проектов, у
    которых для видов зон ЭТОГО участка есть решение определённого стиля
    (нейтральные изгородь и кольцо стиль не задают). Проект с решениями
    разных стилей делит свой голос между ними поровну. (None, None) -- ни у
    одного достаточно похожего проекта стиль не определён."""
    weight: dict[str, float] = {}
    lead: dict[str, str] = {}
    counted = 0
    for neighbor in ranked:
        if counted >= k or neighbor.similarity < MIN_VOTE_SIMILARITY:
            break
        styles = _styles_of(neighbor.slug, kinds, pattern_log)
        if not styles:
            continue
        for style in sorted(styles):
            weight[style] = weight.get(style, 0.0) + neighbor.similarity / len(styles)
            lead.setdefault(style, neighbor.slug)
        counted += 1
    if not weight:
        return None, None
    style = max(sorted(weight), key=lambda st: weight[st])
    return style, lead[style]


def _styles_of(slug: str, kinds: set[ZoneKind], pattern_log: dict[str, dict[ZoneKind, PatternRecord]]) -> set[str]:
    return {
        PATTERN_LIBRARY[record.pattern].style
        for kind, record in pattern_log.get(slug, {}).items()
        if kind in kinds and kind in PATTERN_LIBRARY[record.pattern].zone_kinds
    } - {"neutral"}


def _lead_of_style(
    style: str,
    kinds: set[ZoneKind],
    ranked: list[NeighborMatch],
    pattern_log: dict[str, dict[ZoneKind, PatternRecord]],
) -> str | None:
    """Самый похожий проект (не ниже MIN_VOTE_SIMILARITY) с решением в
    заданном стиле для видов зон участка; None -- таких нет, зоны получат
    типовые приёмы стиля."""
    for neighbor in ranked:
        if neighbor.similarity < MIN_VOTE_SIMILARITY:
            return None
        if style in _styles_of(neighbor.slug, kinds, pattern_log):
            return neighbor.slug
    return None


def _voters(
    kind: ZoneKind,
    ranked: list[NeighborMatch],
    pattern_log: dict[str, dict[ZoneKind, PatternRecord]],
    k: int,
    site_style: str | None = None,
) -> list[tuple[NeighborMatch, PatternRecord]]:
    """k самых похожих проектов с решением для этого вида зоны в стиле
    участка или нейтральным. Проект без такого решения места избирателя не
    занимает; проекты со сходством ниже MIN_VOTE_SIMILARITY не голосуют."""
    voters: list[tuple[NeighborMatch, PatternRecord]] = []
    if k <= 0:
        return voters
    for neighbor in ranked:
        if neighbor.similarity < MIN_VOTE_SIMILARITY:
            break
        record = pattern_log.get(neighbor.slug, {}).get(kind)
        # Приём, не подходящий этому виду зоны, не голосует.
        if record is None or kind not in PATTERN_LIBRARY[record.pattern].zone_kinds:
            continue
        # Единый стиль: приём другого стиля в этом участке не голосует.
        if site_style is not None and PATTERN_LIBRARY[record.pattern].style not in (site_style, "neutral"):
            continue
        voters.append((neighbor, record))
        if len(voters) == k:
            break
    # Заливка без замысла -- только если других решений для вида нет.
    with_concept = [(n, r) for n, r in voters if r.pattern != "generic_fill"]
    return with_concept or voters
