"""
Геометрические примитивы парсера: точки контура сущности DXF, центроид,
буфер отрезка и Transform -- перевод координат чертежа в координаты сцены
(масштаб по $INSUNITS, центрирование по границе участка, ось Y -> Z).
"""

import math


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


# ---------------------------------------------------------------------------
