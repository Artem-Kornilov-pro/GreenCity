"""
Назначение паттернов зонам -- GreenPlan, Этап 4 (issue #23, "Назначение
зон/паттернов"), правилами и без LLM. Опциональное ранжирование
отфильтрованного списка LLM, которое issue #23 допускает для
неоднозначных случаев, здесь сознательно не реализовано: LLM в GreenPlan
только пересказывает готовые решения (decision_report.py). Вместо неё
голос ближайших соседей retrieval-корпуса (pattern_retrieval.nearest_projects)
взвешенный их сходством и есть правило ранжирования: чем более похож
прошлый проект, тем больше у его выбора вес.

Решение в два уровня -- сначала общее на участок, потом по зонам, чтобы у
участка был единый стиль:

1. Стиль участка (site_style): k самых похожих проектов, у которых есть
   решение в определённом стиле (регулярный / пейзажный, см.
   pattern_library.PatternStyle) для видов зон этого участка, голосуют
   сходством. Ведущий проект -- самый похожий проект победившего стиля.
   Раньше стиля не было вовсе: каждый вид зоны голосовал сам по себе, а
   проекты корпуса описаны частично (одним-двумя видами зон), и участок
   собирался из приёмов трёх-четырёх проектов разного стиля -- волны
   рядом с диагональными рядами в одном дворе.
2. Приёмы зон: для каждого вида зоны голосуют k самых похожих проектов с
   решением для этого вида В СТИЛЕ УЧАСТКА (или нейтральным -- изгородь,
   кольцо у здания), зоны вида делятся между их приёмами пропорционально
   весу голосов (по площади). Заливка без замысла (generic_fill) голосует,
   только если других решений нет.

Если НИ ОДИН похожий проект (сходство не ниже MIN_VOTE_SIMILARITY) не
использовал этот вид зоны в стиле участка -- берётся типовой приём этого
стиля (pattern_library.default_pattern), а не
молчаливая выдумка: у такого назначения confidence=0.0 и source_project=None,
и по этим полям видно, что это запасной вариант, а не результат retrieval.
Это ровно то поле, на которое ляжет Этап 6 (отчёт решений, "по аналогии с
проектом X" / без пометки -- по умолчанию), без переделки структуры.
"""

from __future__ import annotations

from pydantic import BaseModel

from core.schemas import Scene
from greenplan.pattern_corpus import CORPUS_SLUGS, PatternRecord, load_pattern_log
from greenplan.pattern_library import PATTERN_LIBRARY, default_pattern
from greenplan.pattern_retrieval import NeighborMatch, nearest_projects
from greenplan.site_characterization import SiteCharacteristics, characterize_site
from greenplan.zone_partitioning import GeometricZone, ZoneKind

# Ниже этого косинусного сходства проект -- не аналог, а просто ближайший из
# далёких: его приём попал бы в записку как "по аналогии с проектом X" при
# сходстве 0.09 (так эталон 25_classical_building_ring_primer получал
# открытые зоны от 28_formal_bosque_primer). Такие соседи не голосуют; не
# осталось никого -- паттерн по умолчанию с пометкой "проверить". Замер
# (корпус leave-one-out + location_old, k=3): доля площади зон на запасном
# паттерне 3.8% без порога, 5.6% при 0.2, 7.3% при 0.3 -- но при 0.3 три
# участка целиком остаются без аналогов. Медиана сходства голосующих -- 0.43.
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
    # Виды растений, подобранные для этой зоны (species_selection.py), и
    # основание подбора -- заполняет deterministic_placement.generate_for_scene
    # после расстановки; assign_patterns их не знает.
    tree_species: list[str] = []
    bush_species: list[str] = []
    species_basis: str | None = None
    # Общее решение на участок (одинаково у всех зон): стиль ("regular" /
    # "landscape", None -- у похожих проектов стиль не определён) и ведущий
    # проект-аналог этого стиля.
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
    """k самых похожих проектов, у которых есть решение для зоны этого вида в
    стиле участка (или нейтральное).
    Раньше брались k ближайших вообще, и проект без записи для вида (например
    02_peschany_pereulok без open_area) просто занимал место соседа -- за
    open_area типичного двора голосовал один 10_stary_gay, и волны выигрывали
    без конкуренции. Проекты со сходством ниже MIN_VOTE_SIMILARITY не
    голосуют: это не аналог (ниже нуля -- и вовсе противоположный участок)."""
    voters: list[tuple[NeighborMatch, PatternRecord]] = []
    if k <= 0:
        return voters
    for neighbor in ranked:
        if neighbor.similarity < MIN_VOTE_SIMILARITY:
            break
        record = pattern_log.get(neighbor.slug, {}).get(kind)
        # Защита от рассинхронизации data/pattern_corpus.yaml и
        # pattern_library.py: паттерн, семантически не подходящий этому виду
        # зоны, в голосовании не участвует.
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
