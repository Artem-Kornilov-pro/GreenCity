"""
Где генератору по сетке (greenery_generator.py) можно сажать: зоны, от
которых отступаем (с отступом по виду посадки), разрешённые зоны озеленения,
занятые места у существующих объектов, и пространственный индекс зон для
текстового объяснения "почему здесь" у каждой точки.
"""

from __future__ import annotations

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from core.schemas import RestrictionZone, SceneObject
from core.setback_norms import PlantKind, SpeciesArg, setback_for
from core.shapes import polygon_from_points

# Зазор вокруг существующих объектов, кроме зданий (у тех своя зона с
# нормативным отступом).
EXISTING_OBJECT_CLEARANCE_M = 1.5


def _keep_out_shapes(
    restrictions: list[RestrictionZone], plant_kind: PlantKind, species: SpeciesArg = None
) -> list[Polygon]:
    """Зоны, куда генератор не сажает данный вид посадки, расширенные на
    отступ этого вида (у кустарника он меньше, чем у дерева).

    Сюда входят и forbidden, и warning-зоны (парковка, дорожки, ЛЭП): при
    ручной расстановке warning -- только предупреждение, но генератор сам
    туда не сажает.

    species -- все виды, которые могут встать на эту площадь; отступ --
    наибольший среди них (setback_norms.setback_for).
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is None:
            continue
        setback = setback_for(zone.type, plant_kind, zone.minDistance, species=species)
        shapes.append(poly.buffer(setback) if setback > 0 else poly)
    return shapes


def _raw_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """То же самое, что и _keep_out_shapes, но БЕЗ отступа по виду посадки --
    для газона (setback_kind=None в каталоге, см. plant_catalog.py): у голого
    дёрна нет ни корней, ни кроны, поэтому норм в setback_norms.py для него
    нет, и достаточно не залезать на саму охранную зону (она уже отбуферена
    на minDistance сети самим парсером, см. parser/parse_dxf.py)."""
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is not None:
            shapes.append(poly)
    return shapes


def _planting_zone_shapes(restrictions: list[RestrictionZone]) -> list[Polygon]:
    """Разрешённые зоны (severity=allowed, например газон). Если они есть,
    генератор сажает только в них.
    """
    shapes: list[Polygon] = []
    for zone in restrictions:
        if zone.severity != "allowed" or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is not None:
            shapes.append(poly)
    return shapes


def _existing_object_shapes(objects: list[SceneObject], clearance: float = EXISTING_OBJECT_CLEARANCE_M) -> list[Polygon]:
    """Круги вокруг существующих объектов, кроме зданий, -- чтобы не ставить
    новый объект поверх старого. clearance для кустов и газона меньше, чем
    для деревьев.
    """
    return [
        Point(obj.position.x, obj.position.z).buffer(clearance)
        for obj in objects
        if obj.type != "building"
    ]


class _ZoneIndex:
    """Многоугольники зон и STRtree для _placement_reason -- строятся один
    раз на вызов генератора, а не на каждую точку."""

    def __init__(self, restrictions: list[RestrictionZone]):
        self.polys: list[Polygon] = []
        self.zones: list[RestrictionZone] = []
        for zone in restrictions:
            if len(zone.polygon) < 3:
                continue
            poly = polygon_from_points(zone.polygon)
            if poly is None:
                continue
            self.polys.append(poly)
            self.zones.append(zone)
        self.tree = STRtree(self.polys) if self.polys else None


def _placement_reason(x: float, z: float, zone_index: _ZoneIndex) -> list[str]:
    """Человекочитаемое объяснение размещения для metadata.reason -- формат
    из ТЗ п.17 (пример: "внутри зоны озеленения", "4.2 м до водопровода").
    """
    reasons = ["внутри допустимой зоны озеленения"]
    if zone_index.tree is not None:
        pt = Point(x, z)
        idx = int(zone_index.tree.nearest(pt))
        poly = zone_index.polys[idx]
        zone = zone_index.zones[idx]
        # boundary, а не exterior: починенный самопересекающийся контур --
        # мультиполигон (core/shapes.py).
        distance = 0.0 if poly.contains(pt) else poly.boundary.distance(pt)
        reasons.append(f"{distance:.1f} м до ближайшего ограничения ({zone.name})")
    return reasons
