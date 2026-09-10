"""
Геометрический планировщик для правок плана текстом (llm_editor.py).

Модель задаёт НАМЕРЕНИЕ -- "кустарники вдоль дорожек", "четыре дерева в центре
двора", "убрать фонари у парковки", -- а координаты считает этот модуль.
Первая версия заставляла модель выдавать координаты самой, и она с этим не
справлялась: на "размести кустарники вдоль дорожек" поставила 5 кустов -- на
дорожке, на парковке, у стены дома и вплотную к фонарям, хотя контуры дорожек
были в контексте полностью. "Вдоль дорожки" -- это линия, смещённая от
контура, шаг посадки и проверка каждой точки: такую геометрию надёжно считает
код, а не языковая модель.

Правила размещения одинаковы для всех операций:
* внутри участка, не вплотную к его границе;
* вне запретных (forbidden) И предупреждающих (warning) зон, расширенных на
  нормативный отступ для вида посадки. Дорожка, парковка, детская площадка --
  не место для куста, хоть это и не "запрет" в строгом смысле: раньше такое
  разрешалось с предупреждением, и кусты вставали прямо на дорожку;
* не ближе OBJECT_CLEARANCE_M к точечным объектам, а для пар из
  POINT_CLEARANCE_M -- не ближе нормы (дерево -- 4 м от опоры освещения).

Разрешённые зоны (газон) допустимую область не сужают: на всех тестовых
локациях это один полигон на 89-100% участка, включающий пятна зданий и
парковок, -- сверх запретов он ничего не отсекает.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from schemas import RestrictionZone, Scene
from setback_norms import setback_for
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep

# Цели групповых операций (llm_editor.PlaceAlongOp и др.) и их подписи.
TARGET_LABELS = {
    "pedestrian_path": "пешеходные дорожки",
    "road": "дороги",
    "building": "здания",
    "parking": "парковки",
    "playground": "детские площадки",
    "site_boundary": "граница участка",
}

SITE_CLEARANCE_M = 0.5  # не ставим вплотную к границе участка
# Запас от границы допустимой области: без него точка ложится ровно на границу
# и не проходит проверку из-за погрешности float.
REGION_EPS_M = 0.1

OBJECT_CLEARANCE_M = 1.0  # минимум до любого точечного объекта
# (тип существующего объекта) -> {вид новой посадки: минимальное расстояние, м}.
POINT_CLEARANCE_M: dict[str, dict[str, float]] = {
    # СНиП 2.07.01-89*, табл. 4: от опоры осветительной сети до дерева 4 м
    # (для кустарника не нормируется).
    "lamp": {"tree": 4.0},
    # Не норма, а здравый смысл: посадка не должна загораживать подъезд.
    "entrance": {"tree": 5.0, "bush": 2.5},
    # Кроны соседних деревьев не должны срастаться.
    "tree": {"tree": 3.0},
}

# Точку, указанную моделью с нарушением, сдвигаем не дальше этого: "у входа"
# не должно превратиться в "на другом конце двора". Меньше брать нельзя: дерево
# у подъезда по нормам ближе и не встанет -- 5 м от стены, в стороне от дорожки
# к подъезду и в 4 м от фонаря у неё. С лимитом 6 м дерево "у входа"
# отклонялось на всех тестовых локациях.
MAX_SNAP_DISTANCE_M = 10.0

# Запасной ряд чуть дальше от цели: куст, которому в основном ряду помешал
# фонарь или подъезд, встаёт на метр дальше, а не выпадает из ряда.
FALLBACK_ROW_STEP_M = 1.0

# Границы параметров от модели -- защита от вырожденных запросов (шаг 1 см по
# району в несколько километров -- миллионы точек).
MIN_SPACING_M = 0.8
MAX_SPACING_M = 50.0
MAX_BULK_PLACEMENTS = 300
MAX_AREA_RADIUS_M = 300.0

_INDEX_CELL_M = 2.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _geometry(points: list, single: bool = False):
    """Полигон из контура сцены. Самопересекающиеся контуры из DXF чинятся
    buffer(0), а не отбрасываются: пропущенная запретная зона -- это посадка
    прямо на газопровод."""
    if len(points) < 3:
        return None
    geom = Polygon([(p.x, p.z) for p in points])
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty or geom.area <= 0:
        return None
    if single and geom.geom_type != "Polygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    return geom


def _polygons(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


class PointIndex:
    """Сетка-хеш точек: "есть ли кто-то ближе r" без перебора всех объектов
    сцены. В районе больше тысячи объектов, а групповая операция проверяет
    тысячи кандидатов -- перебор был бы квадратичным."""

    def __init__(self, cell: float = _INDEX_CELL_M):
        self._cell = max(cell, 0.5)
        self._cells: dict[tuple[int, int], dict[str, tuple[float, float, str]]] = {}
        self._where: dict[str, tuple[int, int]] = {}

    def _key(self, x: float, z: float) -> tuple[int, int]:
        return math.floor(x / self._cell), math.floor(z / self._cell)

    def add(self, key: str, x: float, z: float, obj_type: str = "") -> None:
        self.remove(key)
        cell = self._key(x, z)
        self._cells.setdefault(cell, {})[key] = (x, z, obj_type)
        self._where[key] = cell

    def remove(self, key: str) -> None:
        cell = self._where.pop(key, None)
        if cell is not None:
            self._cells[cell].pop(key, None)

    def within(self, x: float, z: float, radius: float):
        """(ключ, тип, расстояние) для всех точек ближе radius."""
        ci, cj = self._key(x, z)
        span = math.ceil(radius / self._cell)
        for i in range(ci - span, ci + span + 1):
            for j in range(cj - span, cj + span + 1):
                bucket = self._cells.get((i, j))
                if not bucket:
                    continue
                for key, (px, pz, obj_type) in bucket.items():
                    distance = math.hypot(px - x, pz - z)
                    if distance < radius:
                        yield key, obj_type, distance

    def has_within(self, x: float, z: float, radius: float) -> bool:
        return next(self.within(x, z, radius), None) is not None


class Placer:
    """Допустимые места для объектов на одной сцене. Создаётся на запрос и
    переиспользуется всеми операциями плана: допустимая область строится один
    раз на вид посадки (объединение тысяч зон района -- дорогая операция), а
    индекс объектов обновляется по мере добавления и удаления."""

    def __init__(self, scene: Scene):
        self.zones: list[tuple[RestrictionZone, object]] = []
        for zone in scene.restrictions:
            geom = _geometry(zone.polygon)
            if geom is not None:
                self.zones.append((zone, geom))

        self.site = _geometry(scene.boundary.polygon, single=True) if scene.boundary else None
        if self.site is None and self.zones:
            # Без границы участка -- рамка вокруг всех зон, чтобы правки
            # текстом не отказывали целиком.
            self.site = unary_union([geom for _, geom in self.zones]).envelope

        self._regions: dict = {}
        self._targets: dict = {}
        self._free_areas: Optional[list[Polygon]] = None
        self._objects = PointIndex()
        for obj in scene.objects:
            if obj.type != "building":
                self._objects.add(obj.id, obj.position.x, obj.position.z, obj.type)

    # --- Нормы и допустимая область ----------------------------------------

    @staticmethod
    def required(zone: RestrictionZone, kind: Optional[str]) -> float:
        """Нормативный отступ посадки вида kind от зоны. МАФ и мощению (kind
        None) нормы для растений неприменимы -- им нельзя лишь попадать внутрь."""
        if kind in ("tree", "bush"):
            return setback_for(zone.type, kind, zone.minDistance)
        return 0.0

    def region(self, kind: Optional[str]):
        """(область, её prepared-версия), где можно ставить объект вида kind:
        участок минус запретные и предупреждающие зоны, расширенные на
        нормативный отступ."""
        if kind not in self._regions:
            area = None
            if self.site is not None:
                area = self.site.buffer(-SITE_CLEARANCE_M)
                keep_out = [
                    geom.buffer(self.required(zone, kind) + REGION_EPS_M)
                    for zone, geom in self.zones
                    if zone.severity != "allowed"
                ]
                if keep_out:
                    area = area.difference(unary_union(keep_out))
                if area.is_empty:
                    area = None
            self._regions[kind] = (area, prep(area) if area is not None else None)
        return self._regions[kind]

    def blocker(self, x: float, z: float, kind: Optional[str], clearance: float = OBJECT_CLEARANCE_M):
        """(id, нужное расстояние, фактическое) первого мешающего точечного
        объекта или None."""
        reach = max([clearance, *(norms.get(kind, 0.0) for norms in POINT_CLEARANCE_M.values())])
        for key, obj_type, distance in self._objects.within(x, z, reach):
            required = max(clearance, POINT_CLEARANCE_M.get(obj_type, {}).get(kind, 0.0))
            if distance < required:
                return key, required, distance
        return None

    def in_region(self, x: float, z: float, kind: Optional[str]) -> bool:
        """Точка вне зон ограничений с отступами -- без учёта точечных объектов."""
        _, prepared = self.region(kind)
        return prepared is not None and prepared.contains(Point(x, z))

    def is_free(self, x: float, z: float, kind: Optional[str], clearance: float = OBJECT_CLEARANCE_M) -> bool:
        if not self.in_region(x, z, kind):
            return False
        return self.blocker(x, z, kind, clearance) is None

    def explain(self, x: float, z: float, kind: Optional[str], clearance: float = OBJECT_CLEARANCE_M) -> str:
        """Человекочитаемая причина, почему в точке ставить нельзя. Точный
        разбор по зонам медленнее is_free(), поэтому только для точечных
        операций, где причина нужна в сообщении."""
        point = Point(x, z)
        if self.site is not None:
            if not self.site.contains(point):
                return "за границей участка"
            if self.site.exterior.distance(point) < SITE_CLEARANCE_M:
                return "вплотную к границе участка"
        for zone, geom in self.zones:
            if zone.severity == "allowed":
                continue
            if geom.contains(point):
                return f"внутри зоны: {zone.message}"
            required = self.required(zone, kind)
            distance = geom.distance(point)
            if distance < required:
                return f"{zone.message} (до зоны {distance:.1f} м, нужно {required:.1f} м)"
        hit = self.blocker(x, z, kind, clearance)
        if hit is not None:
            key, required, distance = hit
            return f"рядом {key} (до него {distance:.1f} м, нужно {required:.1f} м)"
        return "у самой границы зоны ограничений"

    def nearest_free(self, x: float, z: float, kind: Optional[str], clearance: float = OBJECT_CLEARANCE_M):
        """Сама точка, если там можно, иначе ближайшая допустимая не дальше
        MAX_SNAP_DISTANCE_M; None -- если такой нет."""
        if self.is_free(x, z, kind, clearance):
            return x, z
        area, _ = self.region(kind)
        if area is None:
            return None

        # Сначала точная ближайшая точка области -- у стены это ровно
        # нормативный отступ. Она лежит на самой границе, поэтому чуть
        # заходим внутрь.
        origin = Point(x, z)
        _, near = nearest_points(origin, area)
        distance = origin.distance(near)
        if 0 < distance <= MAX_SNAP_DISTANCE_M:
            factor = (distance + 0.05) / distance
            nx, nz = x + (near.x - x) * factor, z + (near.y - z) * factor
            if self.is_free(nx, nz, kind, clearance):
                return nx, nz

        # Не вышло (например, там фонарь) -- перебираем кольца вокруг точки,
        # примерно через 0.5 м и по радиусу, и по дуге.
        for step in range(1, int(MAX_SNAP_DISTANCE_M / 0.5) + 1):
            radius = step * 0.5
            samples = max(12, math.ceil(2 * math.pi * radius / 0.5))
            for k in range(samples):
                angle = 2 * math.pi * k / samples
                cx, cz = x + radius * math.cos(angle), z + radius * math.sin(angle)
                if self.is_free(cx, cz, kind, clearance):
                    return cx, cz
        return None

    def occupy(self, key: str, x: float, z: float, obj_type: str) -> None:
        self._objects.add(key, x, z, obj_type)

    def release(self, key: str) -> None:
        self._objects.remove(key)

    # --- Цели групповых операций ------------------------------------------

    def _target_zones(self, target: str) -> list:
        if target == "parking":
            # У парковки в парсере тип "custom" -- тот же, что у теплосети,
            # поэтому различаем по слою.
            return [(zone, geom) for zone, geom in self.zones if "PARK" in zone.name.upper()]
        zone_type = "playground_zone" if target == "playground" else target
        return [(zone, geom) for zone, geom in self.zones if zone.type == zone_type]

    def target_geometry(self, target: str):
        """Геометрия цели или None, если такой цели на участке нет."""
        if target not in self._targets:
            if target == "site_boundary":
                geom = self.site
            else:
                parts = [geom for _, geom in self._target_zones(target)]
                geom = unary_union(parts) if parts else None
            self._targets[target] = geom if geom is not None and not geom.is_empty else None
        return self._targets[target]

    def target_summary(self, target: str) -> Optional[tuple[int, float]]:
        """(сколько объектов, длина контура в метрах) -- для контекста модели."""
        geom = self.target_geometry(target)
        if geom is None:
            return None
        if target == "site_boundary":
            return 1, geom.exterior.length
        return len(self._target_zones(target)), geom.boundary.length

    def target_distance(self, target: str, x: float, z: float) -> Optional[float]:
        """Расстояние до цели; для границы участка -- до её линии, а не до
        полигона, внутри которого стоят все объекты."""
        geom = self.target_geometry(target)
        if geom is None:
            return None
        point = Point(x, z)
        return geom.exterior.distance(point) if target == "site_boundary" else geom.distance(point)

    def min_offset(self, target: str, kind: Optional[str], half_depth: float) -> float:
        """Отступ ряда от контура цели: нормативный отступ плюс большая часть
        радиуса кроны, чтобы куст не нависал над дорожкой."""
        if target == "site_boundary":
            base = SITE_CLEARANCE_M
        else:
            base = max((self.required(zone, kind) for zone, _ in self._target_zones(target)), default=0.0)
        return base + 2 * REGION_EPS_M + 0.8 * half_depth

    def points_along(self, target: str, spacing: float, offset: float) -> list[tuple[float, float, float]]:
        """Точки (x, z, поворот в градусах) на линии, смещённой от контура цели
        на offset, с шагом не меньше spacing. Поворот -- по касательной к
        линии: секции живой изгороди и лавки встают вдоль ряда, а не поперёк."""
        geom = self.target_geometry(target)
        if geom is None:
            return []
        band = geom.buffer(-offset) if target == "site_boundary" else geom.buffer(offset)

        points: list[tuple[float, float, float]] = []
        for poly in _polygons(band):
            for ring in (poly.exterior, *poly.interiors):
                length = ring.length
                if length < 1.0:
                    continue
                count = max(1, int(length // spacing))
                for i in range(count):
                    d = i * length / count
                    p = ring.interpolate(d)
                    ahead = ring.interpolate((d + 0.5) % length)
                    tx, tz = ahead.x - p.x, ahead.y - p.y
                    # Фронтенд рисует объект с rotation=[0, θ, 0], а в three.js
                    # такой поворот переводит (1, 0, 0) в (cosθ, 0, -sinθ) --
                    # так локальная ось X (длина секции изгороди) ложится по
                    # касательной.
                    rotation = math.degrees(math.atan2(-tz, tx)) if (tx or tz) else 0.0
                    # shapely хранит точку как (x, y); наша плоскость земли -- (x, z).
                    points.append((p.x, p.y, rotation))
        return points

    def points_in_area(
        self,
        kind: Optional[str],
        step: float,
        within=None,
        max_candidates: int = 2000,
    ) -> list[tuple[float, float]]:
        """Кандидаты на гексагональной сетке внутри допустимой области (и
        внутри within, если задано). Шаг не мельче, чем нужно для
        max_candidates точек: на огромном участке иначе были бы миллионы."""
        area, prepared = self.region(kind)
        if area is None:
            return []
        if within is not None:
            area = area.intersection(within)
            if area.is_empty:
                return []
            prepared = prep(area)

        min_x, min_z, max_x, max_z = area.bounds
        step = max(
            step,
            math.sqrt(area.area / max(max_candidates, 1)),
            # Узкая диагональная полоса: площадь мала, а рамка огромна.
            math.sqrt((max_x - min_x) * (max_z - min_z) / (20 * max(max_candidates, 1))),
        )
        row_height = step * math.sqrt(3) / 2
        points: list[tuple[float, float]] = []
        row = 0
        z = min_z + row_height / 2
        while z <= max_z:
            x = min_x + step / 4 + (step / 2 if row % 2 else 0.0)
            while x <= max_x:
                if prepared.contains(Point(x, z)):
                    points.append((x, z))
                x += step
            z += row_height
            row += 1
        return points

    def free_areas(self, limit: int = 6, min_area_m2: float = 25.0) -> list[Polygon]:
        """Крупнейшие связные куски области, где можно сажать деревья (самый
        строгий вид). Модели -- "где во дворе есть место", place_in_area --
        адресуемые области A1, A2, ... (порядок стабилен для одной сцены)."""
        if self._free_areas is None:
            area, _ = self.region("tree")
            parts = [poly for poly in _polygons(area) if poly.area >= min_area_m2]
            parts.sort(key=lambda poly: -poly.area)
            self._free_areas = parts[:limit]
        return self._free_areas


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
