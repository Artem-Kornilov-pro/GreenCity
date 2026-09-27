"""
Разбиение пригодной площади участка на геометрические зоны, куда затем
назначаются приёмы: линейные -- в вытянутые полосы вдоль границ, площадные --
в открытые участки.

Четыре вида зон, по убыванию приоритета:
1. building_border -- полоса за нормативным отступом от здания;
2. path_corridor -- полоса вдоль дорожек и проезжей части;
3. site_edge -- полоса вдоль внешней границы участка;
4. open_area -- всё остальное.

Зона, близкая и к дому, и к дорожке, достаётся дому. Каждый несвязный кусок
становится отдельной GeometricZone, чтобы приём назначался ему независимо.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split as shapely_split
from shapely.ops import unary_union

from core.schemas import Point2, RestrictionZone, Scene
from core.setback_norms import setback_for
from core.shapes import polygon_from_points
from greenplan.site_characterization import usable_planting_area

ZoneKind = Literal["building_border", "path_corridor", "site_edge", "open_area"]

# Ширина полосы за нормативным отступом (сам отступ уже вычтен из пригодной
# площади). Подобрана так, чтобы вмещать однорядную посадку; нормативного
# обоснования у числа нет.
BUILDING_BORDER_BAND_M = 3.0
PATH_CORRIDOR_BAND_M = 3.0
SITE_EDGE_BAND_M = 3.0

# Мелкие обрезки после buffer/difference (доли м^2 от численных погрешностей
# GEOS на стыках) -- не отдельная зона, а шум.
MIN_ZONE_AREA_SQM = 1.0


class GeometricZone(BaseModel):
    id: str
    kind: ZoneKind
    polygon: list[Point2]
    area_sqm: float


def _split_out_hole(poly: Polygon) -> list[Polygon]:
    """Разрезает многоугольник с дыркой на куски без дыр линией через
    центроид дырки: GeometricZone.polygon -- один контур. На компактных
    участках полосы у края получаются кольцом, и без разреза кольцо
    превратилось бы в сплошной диск. Разрез, а не «перемычка» нулевой
    ширины: перемычка даёт невалидный для GEOS контур."""
    if not poly.interiors:
        return [poly]
    hole_centroid = Polygon(poly.interiors[0]).centroid
    minx, miny, maxx, maxy = poly.bounds
    reach = math.hypot(maxx - minx, maxy - miny) * 2 + 1.0
    for cutter in (
        LineString([(hole_centroid.x - reach, hole_centroid.y), (hole_centroid.x + reach, hole_centroid.y)]),
        LineString([(hole_centroid.x, hole_centroid.y - reach), (hole_centroid.x, hole_centroid.y + reach)]),
    ):
        pieces = list(shapely_split(poly, cutter).geoms)
        if len(pieces) > 1:
            result = []
            for piece in pieces:
                if piece.geom_type == "Polygon" and piece.area >= MIN_ZONE_AREA_SQM:
                    result.extend(_split_out_hole(piece))
            return result
    # Разрезать не удалось -- возвращаем многоугольник как есть, чтобы не
    # потерять зону.
    return [poly]


def _iter_polygons(geom: BaseGeometry):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield from _split_out_hole(geom)
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geom.geoms:
            yield from _iter_polygons(part)


def _emit(geom: BaseGeometry, kind: ZoneKind) -> list[GeometricZone]:
    zones = []
    for i, poly in enumerate(_iter_polygons(geom)):
        if poly.area < MIN_ZONE_AREA_SQM:
            continue
        coords = list(poly.exterior.coords)[:-1]
        zones.append(
            GeometricZone(
                id=f"{kind}_{i + 1:03d}",
                kind=kind,
                polygon=[Point2(x=x, z=z) for x, z in coords],
                area_sqm=round(poly.area, 2),
            )
        )
    return zones


def _band_and_consume(
    restrictions: list[RestrictionZone], types: set[str], band_width: float, remaining: BaseGeometry
) -> tuple[BaseGeometry | None, BaseGeometry]:
    """Полоса шириной band_width сразу за нормативным отступом зон данных
    типов, пересечённая с ещё не занятой площадью, + эта же площадь (полоса
    ПЛЮС сам норматив отступа под ней) вычтена из remaining -- второй раз тот
    же кусок уже никому не достанется, даже если он не попал в саму полосу
    (например потому что он снаружи remaining)."""
    buffered = []
    for zone in restrictions:
        if zone.type not in types or len(zone.polygon) < 3:
            continue
        poly = polygon_from_points(zone.polygon)
        if poly is None:
            continue
        setback = setback_for(zone.type, "tree", zone.minDistance)
        buffered.append(poly.buffer(setback + band_width))
    if not buffered:
        return None, remaining

    outer = unary_union(buffered)
    band = outer.intersection(remaining)
    new_remaining = remaining.difference(outer)
    return (None if band.is_empty else band), new_remaining


def partition_zones(scene: Scene, usable: BaseGeometry | None = None) -> list[GeometricZone]:
    """usable -- уже посчитанная пригодная площадь, чтобы не считать её
    дважды; None -- посчитать здесь."""
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = polygon_from_points(scene.boundary.polygon, single=True)

    if boundary_poly is None:
        return []

    remaining = usable if usable is not None else usable_planting_area(scene)
    if remaining.is_empty:
        return []

    zones: list[GeometricZone] = []

    building_band, remaining = _band_and_consume(scene.restrictions, {"building"}, BUILDING_BORDER_BAND_M, remaining)
    if building_band is not None:
        zones += _emit(building_band, "building_border")

    path_band, remaining = _band_and_consume(
        scene.restrictions, {"pedestrian_path", "road"}, PATH_CORRIDOR_BAND_M, remaining
    )
    if path_band is not None:
        zones += _emit(path_band, "path_corridor")

    # Полоса внутрь от границы участка. Если участок уже двух полос, он
    # целиком -- «край».
    edge_ring = boundary_poly.difference(boundary_poly.buffer(-SITE_EDGE_BAND_M))
    edge_band = edge_ring.intersection(remaining)
    if not edge_band.is_empty:
        zones += _emit(edge_band, "site_edge")
        remaining = remaining.difference(edge_ring)

    if not remaining.is_empty:
        zones += _emit(remaining, "open_area")

    return zones
