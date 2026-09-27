"""
Признаки участка для поиска аналогов: площадь, доля пригодной под
озеленение площади, число зданий, состав зон ограничений, тип территории.

Тип территории -- по правилам, без обучения: категории классификации
территорий ассортимента Москвы, различимые по геометрии. Проверяются по
порядку, первое совпавшее побеждает:
- двор -- детская площадка и хотя бы одно здание;
- улица -- проезжая часть и вытянутая граница (стороны 3:1 и больше);
- площадь -- компактная территория почти без застройки, в основном мощение;
- парк_сквер -- без зданий, большая доля пригодной под озеленение площади;
- промышленная_охранная -- без зданий, большая доля под охранными зонами сетей;
- неопределено -- ни одно правило не сработало (в вектор признаков не входит).
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.placement_geometry import rect_sides
from core.schemas import Scene
from core.setback_norms import setback_for
from core.shapes import polygon_from_points

TerritoryType = Literal["двор", "улица", "площадь", "парк_сквер", "промышленная_охранная", "неопределено"]

# Доля площади под охранными зонами сетей, с которой территория -- промышленная/охранная.
INDUSTRIAL_ZONE_SHARE = 0.4
# Отношение сторон описанного прямоугольника, с которого граница -- вытянутая (улица).
STREET_ELONGATION_RATIO = 3.0
# Меньше этой площади форма и доли слишком шумные для классификации.
MIN_AREA_FOR_OPEN_TYPES_SQM = 2000.0
PARK_PLANTABLE_RATIO = 0.7
SQUARE_MAX_PLANTABLE_RATIO = 0.35

ENGINEERING_ZONE_TYPES = ("gas_pipeline", "sewer", "water_pipeline", "electrical")


class SiteCharacteristics(BaseModel):
    total_area_sqm: float
    plantable_area_sqm: float
    plantable_ratio: float
    building_count: int
    restriction_zone_counts: dict[str, int]
    existing_tree_species: dict[str, int]
    territory_type: TerritoryType
    territory_type_is_heuristic: bool = True


def usable_planting_area(scene: Scene) -> BaseGeometry:
    """Грубая площадь под озеленение: разрешённые зоны (или весь участок)
    минус запретные и предупреждающие зоны с отступом для дерева. Для
    признаков и разбиения на зоны; точные отступы -- при расстановке."""
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return Polygon()

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
        return Polygon()

    allowed = [
        poly
        for poly in (polygon_from_points(zone.polygon) for zone in scene.restrictions if zone.severity == "allowed")
        if poly is not None
    ]
    base = unary_union(allowed) if allowed else boundary_poly

    keep_out = []
    for zone in scene.restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is None:
            continue
        setback = setback_for(zone.type, "tree", zone.minDistance)
        keep_out.append(poly.buffer(setback) if setback > 0 else poly)

    usable = base.intersection(boundary_poly)
    if keep_out:
        usable = usable.difference(unary_union(keep_out))
    return usable


def _boundary_elongation(boundary_poly: Polygon) -> float:
    """Отношение длинной стороны минимального описанного прямоугольника к
    короткой -- >=STREET_ELONGATION_RATIO означает вытянутую (уличную)
    форму границы. rect_sides переиспользован из placement.py (тот же приём,
    что и для "хребта" полосовых зон в centerline_of)."""
    sides = rect_sides(boundary_poly)
    lengths = sorted(side[2] for side in sides)
    short, long_ = lengths[0], lengths[-1]
    return long_ / short if short > 1e-6 else 1.0


def _guess_territory_type(
    zone_counts: dict[str, int],
    zone_areas: dict[str, float],
    building_count: int,
    total_area: float,
    plantable_ratio: float,
    elongation: float,
) -> tuple[TerritoryType, bool]:
    """(тип территории, is_heuristic). Порядок правил значим: специфичное
    раньше общего. is_heuristic=True только для «неопределено» -- ни одно
    правило не сработало."""
    if zone_counts.get("playground_zone", 0) > 0 and building_count >= 1:
        return "двор", False

    industrial_share = sum(zone_areas.get(t, 0.0) for t in ENGINEERING_ZONE_TYPES) / total_area if total_area else 0.0
    if building_count == 0 and industrial_share >= INDUSTRIAL_ZONE_SHARE:
        return "промышленная_охранная", False

    if zone_counts.get("road", 0) > 0 and elongation >= STREET_ELONGATION_RATIO:
        return "улица", False

    if total_area >= MIN_AREA_FOR_OPEN_TYPES_SQM and building_count == 0:
        if plantable_ratio >= PARK_PLANTABLE_RATIO:
            return "парк_сквер", False
        if elongation < STREET_ELONGATION_RATIO and plantable_ratio <= SQUARE_MAX_PLANTABLE_RATIO:
            return "площадь", False

    return "неопределено", True


def characterize_site(scene: Scene, usable: Optional[BaseGeometry] = None) -> Optional[SiteCharacteristics]:
    """Признаки участка; None -- у сцены нет границы. usable -- уже
    посчитанная площадь под озеленение, чтобы не считать её дважды."""
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return None

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
        return None

    total_area = boundary_poly.area
    if usable is None:
        usable = usable_planting_area(scene)
    plantable_area = 0.0 if usable.is_empty else usable.area

    zone_counts = dict(Counter(zone.type for zone in scene.restrictions))
    zone_areas: Counter[str] = Counter()
    for zone in scene.restrictions:
        if len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is not None:
            zone_areas[zone.type] += poly.area

    species_counts: Counter[str] = Counter()
    for obj in scene.objects:
        if obj.type != "tree":
            continue
        species = obj.metadata.get("species")
        if species:
            species_counts[species] += 1

    plantable_ratio = round(plantable_area / total_area, 4) if total_area else 0.0
    building_count = scene.meta.buildingCount
    elongation = _boundary_elongation(boundary_poly)
    territory_type, is_heuristic = _guess_territory_type(
        zone_counts, dict(zone_areas), building_count, total_area, plantable_ratio, elongation
    )

    return SiteCharacteristics(
        total_area_sqm=round(total_area, 1),
        plantable_area_sqm=round(plantable_area, 1),
        plantable_ratio=plantable_ratio,
        building_count=building_count,
        restriction_zone_counts=zone_counts,
        existing_tree_species=dict(species_counts),
        territory_type=territory_type,
        territory_type_is_heuristic=is_heuristic,
    )
