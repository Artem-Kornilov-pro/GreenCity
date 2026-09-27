"""
Геометрические примитивы парсера: точки контура сущности DXF, центроид,
буфер отрезка и Transform -- перевод координат чертежа в координаты сцены
(масштаб по $INSUNITS, центрирование по границе участка, ось Y -> Z).
"""

import math

from shapely.geometry import box


def polygon_points(entity):
    """Вернуть список (x, y, z) для LWPOLYLINE/POLYLINE, без дублей подряд."""
    pts = []
    if entity.dxftype() == "LWPOLYLINE":
        elev = entity.dxf.elevation
        for p in entity.get_points():
            pts.append((float(p[0]), float(p[1]), float(elev)))
    elif entity.dxftype() == "POLYLINE":
        for v in entity.vertices:
            loc = v.dxf.location
            pts.append((float(loc.x), float(loc.y), float(loc.z)))
    # убрать подряд идущие почти-дубли (артефакты экспорта) и замыкающую точку
    cleaned = []
    for p in pts:
        if cleaned and math.hypot(p[0] - cleaned[-1][0], p[1] - cleaned[-1][1]) < 1e-6:
            continue
        cleaned.append(p)
    if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1e-6:
        cleaned.pop()
    return cleaned


def centroid(points):
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def buffer_segment(x1, y1, x2, y2, half_width):
    """Прямая (труба/кабель, заданная центральной линией) -> прямоугольная зона-коридор
    шириной 2*half_width вдоль неё (в плане XY, высота/глубина не учитывается)."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    nx, ny = -dy / length * half_width, dx / length * half_width
    return [(x1 + nx, y1 + ny, 0.0), (x2 + nx, y2 + ny, 0.0),
            (x2 - nx, y2 - ny, 0.0), (x1 - nx, y1 - ny, 0.0)]


class Transform:
    """DXF (x, y, z) -> Three.js (x, y=высота, z), с опциональным сдвигом origin."""

    def __init__(self, scale=1.0, origin_x=0.0, origin_y=0.0):
        self.scale = scale
        self.ox = origin_x
        self.oy = origin_y

    def point(self, x, y, z=0.0):
        return {
            "x": round((x - self.ox) * self.scale, 3),
            "y": round(z * self.scale, 3),
            "z": round((y - self.oy) * self.scale, 3),
        }

    def polygon(self, pts):
        return [{"x": round((x - self.ox) * self.scale, 3),
                  "z": round((y - self.oy) * self.scale, 3)} for x, y, *_ in pts]


def split_holes(geom, _depth=0):
    """Полигон с дырками -> список полигонов БЕЗ дырок, покрывающих ту же
    площадь. Зона сцены хранит только внешний контур (плоский список точек),
    и раньше дырки просто выбрасывались: кольцевая трасса газопровода или
    водопровода вокруг квартала превращалась в сплошную охранную зону на весь
    квартал (Харьковская: 4,2 га запрета там, где сетей нет; Олимпийская
    деревня -- 3,7 га). Режем вертикальной линией через середину первой дырки:
    дырка вскрывается с двух сторон и становится вырезом в контуре кусков, а
    не дыркой; повторяем, пока дырок не останется."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [piece for g in geom.geoms for piece in split_holes(g, _depth)]
    if geom.geom_type != "Polygon" or geom.area <= 0:
        return []
    if not geom.interiors:
        return [geom]
    hole_minx, _, hole_maxx, _ = geom.interiors[0].bounds
    if hole_maxx - hole_minx < 1e-9 or _depth > 500:
        # Вырожденная дырка нулевой ширины -- площади у неё нет, резать нечего.
        return [type(geom)(geom.exterior)]
    cut = (hole_minx + hole_maxx) / 2
    minx, miny, maxx, maxy = geom.bounds
    pieces = []
    for half in (box(minx - 1, miny - 1, cut, maxy + 1), box(cut, miny - 1, maxx + 1, maxy + 1)):
        pieces += split_holes(geom.intersection(half), _depth + 1)
    return pieces


# ---------------------------------------------------------------------------
