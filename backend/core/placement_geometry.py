"""
Геометрия расстановки без знания о нормах: выбор мест из кандидатов
(spread_subset, pick_near, pick_spread), обход препятствий (shortest_path),
линии для рядов и приёмов (lines_of, sample_line, centerline_of, rows_of,
wavy_line, concentric_rings_of, rect_sides, rotation_of).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from core.placement import PointIndex

# --- Выбор мест из кандидатов ------------------------------------------------


def spread_subset(points: list, limit: int) -> list:
    """Не больше limit точек, равномерно по списку. Точки ряда идут вдоль
    линии, поэтому и выборка равномерна вдоль ряда, а не "первые N в углу"."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)]


def _greedy(points: list[tuple[float, float]], count: int, min_distance: float) -> list[tuple[float, float]]:
    index = PointIndex(max(min_distance, 0.5))
    chosen: list[tuple[float, float]] = []
    for x, z in points:
        if len(chosen) >= count:
            break
        if index.has_within(x, z, min_distance):
            continue
        index.add(str(len(chosen)), x, z)
        chosen.append((x, z))
    return chosen


def pick_near(
    points: list[tuple[float, float]],
    count: int,
    min_distance: float,
    center: tuple[float, float],
) -> list[tuple[float, float]]:
    """Компактная группа: ближайшие к центру точки с соблюдением шага."""
    ordered = sorted(points, key=lambda p: (p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2)
    return _greedy(ordered, count, min_distance)


def pick_spread(points: list[tuple[float, float]], count: int, min_distance: float) -> list[tuple[float, float]]:
    """Равномерно по области: k-средних (алгоритм Ллойда) по кандидатам, затем
    каждый центр притягивается к ближайшему свободному кандидату -- центр
    кластера в невыпуклом дворе может оказаться на запретной зоне.
    Инициализация детерминированная (самые удалённые точки), поэтому один и
    тот же план даёт один и тот же результат."""
    if len(points) <= count:
        return _greedy(points, count, min_distance)

    pts = np.asarray(points, dtype=float)
    first = int(((pts - pts.mean(axis=0)) ** 2).sum(axis=1).argmin())
    seeds = [first]
    nearest = ((pts - pts[first]) ** 2).sum(axis=1)
    for _ in range(count - 1):
        seed = int(nearest.argmax())
        seeds.append(seed)
        nearest = np.minimum(nearest, ((pts - pts[seed]) ** 2).sum(axis=1))

    centers = pts[seeds].copy()
    for _ in range(10):
        labels = ((pts[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
        for j in range(count):
            members = pts[labels == j]
            if len(members):
                centers[j] = members.mean(axis=0)

    index = PointIndex(max(min_distance, 0.5))
    chosen: list[tuple[float, float]] = []
    taken: set[int] = set()

    def take(i: int) -> bool:
        x, z = points[i]
        if i in taken or index.has_within(x, z, min_distance):
            return False
        taken.add(i)
        index.add(str(i), x, z)
        chosen.append((x, z))
        return True

    for center in centers:
        for i in np.argsort(((pts - center) ** 2).sum(axis=1)):
            if take(int(i)):
                break
    # Центрам, которым не хватило места из-за шага, добираем самыми удалёнными.
    for i in [*seeds, *range(len(points))]:
        if len(chosen) >= count:
            break
        take(i)
    return chosen[:count]


# --- Обход препятствий -------------------------------------------------------
#
# Граф видимости: вершины -- начало, конец и слегка отодвинутые углы
# препятствий, ребро -- если отрезок не проходит сквозь препятствие.
# Кратчайший путь по нему (Дейкстра) огибает помехи минимально.
PATH_CLEARANCE_M = 0.4  # запас от угла препятствия, чтобы дорожка не прижималась к стене вплотную
MAX_OBSTACLES_FOR_ROUTING = 12  # больше -- граф видимости станет неоправданно большим для одной связи


def _segment_visible(a: Point, b: Point, obstacles: list[Polygon]) -> bool:
    """Прямая a-b не проходит сквозь ни одно препятствие. Линия, лишь
    касающаяся препятствия (в общей вершине двух путей в обход), это не
    пересечение "насквозь" -- поэтому Point/пустое пересечение пропускаем, а
    настоящее перекрытие (общий отрезок или площадь) -- нет."""
    if a.distance(b) < 1e-9:
        return True
    line = LineString([(a.x, a.y), (b.x, b.y)])
    for obstacle in obstacles:
        inter = line.intersection(obstacle)
        if inter.is_empty or inter.geom_type == "Point":
            continue
        return False
    return True


def shortest_path(start: Point, end: Point, obstacles: list[Polygon]) -> Optional[LineString]:
    """Кратчайший путь start -> end в обход obstacles, или None, если пути
    нет вовсе (конец сам внутри препятствия и т.п.). Без препятствий или при
    свободной прямой -- она и возвращается; иначе строится и обходится граф
    видимости. Рассчитан на единицы-десятки препятствий (одна связь внутри
    двора, не весь район) -- см. MAX_OBSTACLES_FOR_ROUTING."""
    obstacles = obstacles[:MAX_OBSTACLES_FOR_ROUTING]
    if not obstacles or _segment_visible(start, end, obstacles):
        return LineString([(start.x, start.y), (end.x, end.y)])

    nodes: list[Point] = [start, end]
    for obstacle in obstacles:
        widened = obstacle.buffer(PATH_CLEARANCE_M, join_style=2)  # mitre -- сохраняет острые углы
        rings = [widened.exterior] if widened.geom_type == "Polygon" else [g.exterior for g in widened.geoms]
        for ring in rings:
            nodes.extend(Point(c) for c in ring.coords[:-1])

    n = len(nodes)
    adjacency: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _segment_visible(nodes[i], nodes[j], obstacles):
                d = nodes[i].distance(nodes[j])
                adjacency[i].append((j, d))
                adjacency[j].append((i, d))

    dist = [math.inf] * n
    prev = [-1] * n
    dist[0] = 0.0
    done = [False] * n
    for _ in range(n):
        u = min((i for i in range(n) if not done[i]), key=lambda i: dist[i], default=None)
        if u is None or dist[u] == math.inf:
            break
        done[u] = True
        if u == 1:
            break
        for v, w in adjacency[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                prev[v] = u

    if dist[1] == math.inf:
        return None
    path = [1]
    while path[-1] != 0:
        path.append(prev[path[-1]])
    return LineString([(nodes[i].x, nodes[i].y) for i in reversed(path)])


# --- Линии: сэмплирование и центральная ось ----------------------------------


def lines_of(geom) -> list[LineString]:
    """Плоский список LineString из геометрии любой вложенности (одна линия,
    MultiLineString, GeometryCollection после intersection) -- пересечение
    линии с полигоном не гарантирует один цельный кусок."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [LineString(geom.coords)]
    return [part for g in getattr(geom, "geoms", []) for part in lines_of(g)]


def rotation_of(tx: float, tz: float) -> float:
    """Поворот в градусах для объекта, чья локальная ось X должна лечь по
    касательной (tx, tz) -- как в Placer.points_along: поворот θ кладёт
    локальную ось X по (cosθ, -sinθ) в three.js-координатах (x, z)."""
    return math.degrees(math.atan2(-tz, tx)) if (tx or tz) else 0.0


def sample_line(line: LineString, step: float):
    """(x, z, tx, tz) через step вдоль линии, с отступом от концов поровну;
    (tx, tz) -- единичная касательная в точке."""
    length = line.length
    if length < 1e-6:
        return
    count = max(1, int(length // step))
    first = (length - (count - 1) * step) / 2
    for i in range(count):
        d = first + i * step
        p = line.interpolate(d)
        a, b = line.interpolate(max(d - 0.25, 0.0)), line.interpolate(min(d + 0.25, length))
        tx, tz = b.x - a.x, b.y - a.y
        norm = math.hypot(tx, tz) or 1.0
        yield p.x, p.y, tx / norm, tz / norm


def rect_corners(poly: Polygon) -> list[tuple[float, float]]:
    """Углы минимального описанного прямоугольника. На контурах с почти
    совпадающими соседними вершинами (реальные DXF) GEOS поднимает флаг
    плавающей точки, и shapely печатает "RuntimeWarning: invalid value
    encountered in oriented_envelope" -- сам прямоугольник при этом верный
    (сверено с оболочкой), а лог сервера засыпало десятками строк на запрос."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]


def rect_sides(poly: Polygon) -> list[tuple[float, float, float]]:
    """Две смежные стороны минимального описанного прямоугольника, как
    (единичный вектор x, единичный вектор z, длина стороны)."""
    coords = rect_corners(poly)
    sides = []
    for i in range(2):
        (ax, az), (bx, bz) = coords[i], coords[i + 1]
        length = math.hypot(bx - ax, bz - az) or 1.0
        sides.append(((bx - ax) / length, (bz - az) / length, length))
    return sides


def _rotate(ux: float, uz: float, angle_deg: float) -> tuple[float, float]:
    """Единичный вектор (ux, uz), повёрнутый на angle_deg градусов."""
    if angle_deg == 0.0:
        return ux, uz
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return ux * cos_t - uz * sin_t, ux * sin_t + uz * cos_t


def centerline_of(poly: Polygon, angle_offset_deg: float = 0.0):
    """Ось вытянутой зоны: прямая через центроид вдоль длинной стороны
    минимального описанного прямоугольника (с поворотом angle_offset_deg для
    диагональных рядов), обрезанная по контуру. Для изогнутой зоны может
    дать несколько кусков. None -- зона слишком мала."""
    if poly.is_empty or poly.area <= 0:
        return None
    sides = rect_sides(poly)
    ux, uz, _ = max(sides, key=lambda s: s[2])
    ux, uz = _rotate(ux, uz, angle_offset_deg)
    center = poly.centroid
    min_x, min_z, max_x, max_z = poly.bounds
    far = max(math.hypot(max_x - min_x, max_z - min_z), 1.0) * 2
    ray = LineString([(center.x - ux * far, center.y - uz * far), (center.x + ux * far, center.y + uz * far)])
    clipped = ray.intersection(poly)
    return None if clipped.is_empty else clipped


def wavy_line(line: LineString, amplitude: float, wavelength: float):
    """Линия, изогнутая синусом поперёк направления (волнистые ряды):
    амплитуда amplitude, длина волны wavelength. amplitude <= 0 -- без волны."""
    if amplitude <= 0 or line is None or line.is_empty:
        return line
    length = line.length
    if length < 1e-6:
        return line
    step = min(2.0, wavelength / 8)
    count = max(2, int(length // step) + 1)
    points = []
    for i in range(count + 1):
        t = min(i * step, length)
        p = line.interpolate(t)
        a = line.interpolate(max(t - 0.1, 0.0))
        b = line.interpolate(min(t + 0.1, length))
        tx, tz = b.x - a.x, b.y - a.y
        norm = math.hypot(tx, tz) or 1.0
        tx, tz = tx / norm, tz / norm
        px, pz = -tz, tx  # перпендикуляр к касательной
        offset = amplitude * math.sin(2 * math.pi * t / wavelength)
        points.append((p.x + px * offset, p.y + pz * offset))
        if t >= length:
            break
    return LineString(points) if len(points) >= 2 else line


def rows_of(poly: Polygon, spacing: float, angle_offset_deg: float = 0.0):
    """Параллельные линии вдоль длинной стороны описанного прямоугольника
    с шагом spacing поперёк, каждая обрезана по контуру. Для открытых
    площадей произвольной ширины: один ряд по центру оставил бы остальное
    пустым. Ряды симметричны относительно центроида."""
    if poly.is_empty or poly.area <= 0:
        return []
    sides = rect_sides(poly)
    long_side = max(sides, key=lambda s: s[2])
    short_side = min(sides, key=lambda s: s[2])
    ux, uz, _ = long_side
    ux, uz = _rotate(ux, uz, angle_offset_deg)
    px, pz = -uz, ux  # единичный перпендикуляр к (повёрнутой) длинной стороне
    n_rows = max(1, round(short_side[2] / spacing))
    center = poly.centroid
    min_x, min_z, max_x, max_z = poly.bounds
    far = max(math.hypot(max_x - min_x, max_z - min_z), 1.0) * 2
    start_offset = -(n_rows - 1) / 2 * spacing
    lines = []
    for i in range(n_rows):
        offset = start_offset + i * spacing
        cx, cz = center.x + px * offset, center.y + pz * offset
        ray = LineString([(cx - ux * far, cz - uz * far), (cx + ux * far, cz + uz * far)])
        clipped = ray.intersection(poly)
        if not clipped.is_empty:
            lines.append(clipped)
    return lines


def concentric_rings_of(poly: Polygon, spacing: float):
    """Концентрические окружности вокруг центроида с шагом spacing,
    обрезанные по контуру зоны, -- посадка кольцами вокруг центра."""
    if poly.is_empty or poly.area <= 0 or spacing <= 0:
        return []
    center = poly.centroid
    min_x, min_z, max_x, max_z = poly.bounds
    max_radius = math.hypot(max_x - min_x, max_z - min_z) / 2 + spacing
    rings = []
    radius = spacing
    while radius <= max_radius:
        ring = Point(center.x, center.y).buffer(radius, quad_segs=32).exterior
        clipped = ring.intersection(poly)
        if not clipped.is_empty:
            rings.append(clipped)
        radius += spacing
    return rings
