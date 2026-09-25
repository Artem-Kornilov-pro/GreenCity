"""
Полигон из контура сцены -- одно правило для всех модулей.

Контуры из DXF бывают самопересекающимися (невалидными для GEOS). Их нужно
чинить, а не отбрасывать: раньше часть модулей молча пропускала
такие зоны, и на 20_makeeva_s терялось 140 тыс. м² разрешённой земли (GreenPlan
строил зоны на 2,5 тыс. м² из 198 тыс.), а 113 тыс. м² запретных зон не
вычитались -- проверка нарушений их не видела, генератор по сетке мог
посадить дерево прямо на сеть.

Чинится make_valid, а не привычным buffer(0): у контура-"бабочки" buffer(0)
оставляет только одну петлю (половину площади) -- для запретной зоны это
та же потерянная зона, только наполовину.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def polygon_from_points(points: Sequence, single: bool = False) -> Optional[BaseGeometry]:
    """Полигон (или мультиполигон после починки) из точек с .x/.z; None --
    если площади нет. single=True -- только наибольшая часть (граница
    участка: один контур)."""
    if len(points) < 3:
        return None
    geom = Polygon([(p.x, p.z) for p in points])
    if not geom.is_valid:
        geom = _polygonal(shapely.make_valid(geom))
    if geom is None or geom.is_empty or geom.area <= 0:
        return None
    if single and geom.geom_type != "Polygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    return geom


def _polygonal(geom: BaseGeometry) -> Optional[BaseGeometry]:
    """Только площадные части: make_valid может вернуть коллекцию с
    отрезками и точками (выродившиеся куски контура)."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    polygons = [p for g in parts for p in (g.geoms if g.geom_type == "MultiPolygon" else [g])]
    if not polygons:
        return None
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
