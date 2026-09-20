"""
Разбиение пригодной площади участка на зоны по геометрическому типу -- задел
под Этап 4 GreenPlan (issue #23, "Назначение зон/паттернов"). Это НЕ подбор
паттерна (аллея/живая изгородь/миксбордер и т.п.) -- паттерны фильтруются по
похожим проектам, которых у нас пока нет (Этап 3, retrieval, корпус из 20
проектов ещё не восстановлен). Это подготовка МЕСТА для будущего подбора:
линейные паттерны (аллея, рядовая посадка, живая изгородь) нужно класть в
зоны вытянутой формы вдоль конкретной границы, а площадные (массив,
миксбордер) -- в оставшиеся открытые куски. Без этого разбиения classify
"куда класть паттерн" при появлении Этапа 4 не с чем будет сопоставлять.

Четыре вида зон, по убыванию приоритета захвата площади:
1. building_border -- полоса сразу за нормативным отступом от здания
   (setback_norms.setback_for), для бордюрной посадки/живой изгороди у дома.
2. path_corridor -- полоса вдоль дорожек/проезжей части, для рядовой посадки
   и аллей вдоль маршрутов движения.
3. site_edge -- полоса вдоль внешней границы участка, для живой изгороди по
   периметру (в отличие от building_border -- это край участка, а не край
   здания).
4. open_area -- всё, что осталось: под площадные паттерны (массив, клумба,
   миксбордер).

Приоритет фиксированный (здание > дорожка > край участка > остальное): зона,
близкая одновременно к дому и к дорожке, достаётся дому -- у норматива
отступа от здания больше правовой вес, чем у решения "вдоль чего сажать
аллею". Каждый вид зоны может дать несколько несвязных многоугольников
(например, полосы вдоль двух разных зданий) -- каждый становится отдельной
GeometricZone, а не одной дырявой мультиполигональной записью, чтобы Этап 4
мог назначать паттерн каждому куску независимо.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel
from schemas import Point2, RestrictionZone, Scene
from setback_norms import setback_for
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split as shapely_split
from shapely.ops import unary_union
from site_characterization import usable_planting_area

ZoneKind = Literal["building_border", "path_corridor", "site_edge", "open_area"]

# Ширина полосы ЗА нормативным отступом (не сам отступ -- тот уже вычтен из
# usable_planting_area как запретная зона). Демо-значение: подобрано так,
# чтобы полоса реально вмещала однорядную посадку (кустарник группой ~0.6-1 м
# + запас), обоснования в акте под это число нет -- в отличие от самого
# отступа, который берётся из setback_norms.py.
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
    """Разрезает Polygon с дыркой (interior ring) на простые куски без дыр,
    прямой линией через центроид дырки. GeometricZone.polygon -- плоский
    список точек одного контура, дырки не поддерживает; на КОМПАКТНЫХ
    участках (круг, треугольник, шестигранник -- в отличие от узких
    вытянутых реальных локаций, где такое не возникало) boundary.buffer(-band)
    не пуст, и site_edge/building_border/path_corridor получаются настоящим
    кольцом (Polygon с дыркой). Раньше _emit брал только .exterior.coords,
    отбрасывая дырку -- баг: полигон превращался в сплошной диск, перекрывая
    open_area целиком.

    Резать линией (shapely.ops.split), а не схлопывать дырку "перемычкой"
    нулевой ширины (первая попытка) -- перемычка технически валидна как
    приём, но даёт self-tangent контур, который GEOS помечает invalid, и
    Polygon.contains() у такого контура ведёт себя непредсказуемо возле
    самой перемычки (поймано тестом на круглом участке: угловая точка
    шестигранника у самой перемычки не считалась ни site_edge, ни
    open_area). Разрез линией даёт два обычных выпуклых куска без дыр --
    тот же случай, что уже штатно бывает у building_border с несколькими
    несвязными зданиями (несколько отдельных GeometricZone вместо одной
    дырявой записи), просто тут не несколько зданий, а одна большая дырка."""
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
    # Не удалось разрезать (вырожденный случай) -- лучше честно вернуть
    # дырявый полигон как есть (площадь верна, .exterior потом даст
    # завышенный контур), чем тихо потерять зону целиком.
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
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if not poly.is_valid or poly.area == 0:
            continue
        setback = setback_for(zone.type, "tree", zone.minDistance)
        buffered.append(poly.buffer(setback + band_width))
    if not buffered:
        return None, remaining

    outer = unary_union(buffered)
    band = outer.intersection(remaining)
    new_remaining = remaining.difference(outer)
    return (None if band.is_empty else band), new_remaining


def partition_zones(scene: Scene) -> list[GeometricZone]:
    if scene.boundary is None or len(scene.boundary.polygon) < 3:
        return []

    boundary_poly = Polygon([(p.x, p.z) for p in scene.boundary.polygon])
    if not boundary_poly.is_valid or boundary_poly.area == 0:
        return []

    remaining = usable_planting_area(scene)
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

    # Полоса вдоль ВНЕШНЕЙ границы участка -- в отличие от двух зон выше, это
    # не буфер вокруг restriction-зоны, а кольцо внутрь от boundary. Если
    # участок уже уже, чем удвоенная SITE_EDGE_BAND_M, buffer(-SITE_EDGE_BAND_M)
    # выродится в пустой полигон, и разница совпадёт со всем boundary -- это
    # корректно: узкий участок весь является "краем".
    edge_ring = boundary_poly.difference(boundary_poly.buffer(-SITE_EDGE_BAND_M))
    edge_band = edge_ring.intersection(remaining)
    if not edge_band.is_empty:
        zones += _emit(edge_band, "site_edge")
        remaining = remaining.difference(edge_ring)

    if not remaining.is_empty:
        zones += _emit(remaining, "open_area")

    return zones
