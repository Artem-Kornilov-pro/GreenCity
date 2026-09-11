"""
Полный дизайн двора для правки текстом (операция design_area в llm_editor.py).

"Проложи дорожки, скамейки, урны, фонари и живую изгородь, продумай клумбы" --
это не пять отдельных посадок, а связная композиция. Раньше модель сводила
такую просьбу к одной группе кустов. Теперь модель лишь перечисляет нужные
элементы (и, если хочет, стиль планировки), а раскладку от каркаса к деталям
считает этот модуль:

1. Место под дизайн -- связная часть участка без зданий (с отступом),
   парковок, площадок и дорог. "Двор" -- это в первую очередь пространство
   МЕЖДУ домами, а не любой свободный газон: из связных частей предпочитается
   та, что заметно окружена зданиями с разных сторон (см. _enclosure) и
   выходит хотя бы двумя зданиями; при равенстве -- та, к которой выходят
   подъезды, затем крупнейшая. На участке с одним домом (нет "между") это
   правило не срабатывает, и остаётся обычный выбор по входам/площади.
2. Каркас дорожек -- один из нескольких ШАБЛОНОВ (STYLES), а не одна и та же
   геометрия для любого двора. По умолчанию (и почти всегда при auto) --
   "spine": дорожки идут от подъезда к подъезду напрямую (принцип desire
   line -- кратчайшая связь между точками, откуда/куда реально идут люди;
   см. https://www.landscapearchitecture.org.uk/desire-lines-key-principle-landscape-architecture/),
   а не декоративная фигура. Площадь появляется, только если в нужном узле
   дерева сходится несколько дорожек (см. layout(): hub_uses) или явно нужна
   под фонтан -- в маленьком дворе с парой рядом стоящих подъездов площади
   не будет вовсе, и это правильно: не в каждом дворе она уместна. Остальные
   шаблоны -- на особый случай: "grid" (сетка) для большого двора без явных
   входов рядом или сильно вытянутого; "perimeter" (кольцо по краю) для
   узкого двора, где для площади нет места; "diagonal" (крест по диагоналям
   с площадью на пересечении) -- декоративный вариант только по явной
   просьбе, для auto не выбирается. Подъезд учитывается, только если он
   реально выходит в эту часть двора, а не смотрит на улицу с другой стороны
   тонкого корпуса (см. _faces_courtyard) -- иначе дорожки на плане
   связывали бы дворовую сеть со входом, до которого от неё физически нет
   прохода.
3. Выход на парковку -- если она есть поблизости, сеть дорожек сама к ней
   подходит (та же логика, что и с подъездами), без отдельной просьбы.
4. Детали: фонтан (только по явной просьбе, см. DEFAULT_ELEMENTS) и клумбы
   на площади, парные клумбы у подъездов, фонари зигзагом и скамейки с
   урнами вдоль дорожек, живая изгородь по краю двора (не у фасадов), кусты
   вдоль дорожек. Деревья -- РАЗБРОСОМ по свободной площади двора (см.
   scatter()), а не вдоль дорожки: дорожка обычно идёт всего в 3 м от
   подъезда (см. _entrance_anchor), а дерево требует 5 м от стены -- вдоль
   такой дорожки для дерева свободна одна сторона от силы, и "деревья вдоль
   дорожек" почти всегда отклонялись бы целиком.

Каждый элемент, кроме самих дорожек, проходит проверку планировщика
(placement.Placer: нормы, зоны, соседние объекты) и не встаёт на мощение.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from typing import Optional

from placement import Placer, clamp, pick_spread, polygons
from plant_catalog import CatalogItem
from schemas import Scene
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, polylabel, unary_union

# Элементы, которые может заказать модель, и подписи для отчёта.
ELEMENT_LABELS = {
    "paths": "дорожки",
    "flowerbeds": "клумбы",
    "fountain": "фонтан",
    "lamps": "фонари",
    "benches": "скамейки",
    "trash": "урны",
    "hedge": "живая изгородь",
    "trees": "деревья",
    "bushes": "кусты",
}
ELEMENTS = tuple(ELEMENT_LABELS)
# Фонтан -- НЕ по умолчанию: он не всегда уместен (нужна открытая площадь, а
# она есть не в каждом дворе, см. layout()) и не в каждой просьбе "сделай
# дизайн двора" подразумевается; ставим его только по явной просьбе.
DEFAULT_ELEMENTS = ("paths", "flowerbeds", "lamps", "benches", "trash", "hedge", "trees")
_ELEMENT_TYPES = {
    "paths": "path_segment",
    "flowerbeds": "flowerbed_patch",
    "fountain": "fountain",
    "lamps": "lamp",
    "benches": "bench",
    "trash": "trash",
    "hedge": "hedge_segment",
    "trees": "tree",
    "bushes": "bush",
}
# Позиции базового каталога, из которых собирается дизайн.
DESIGN_ITEM_IDS = ("path_segment", "flowerbed_patch", "fountain", "lamp", "bench", "trash", "hedge_segment")

# Шаблоны каркаса дорожек. "auto" -- выбрать по форме двора (_choose_style);
# среди авто-кандидатов "diagonal" нет -- он декоративный, только по просьбе.
STYLES = ("auto", "spine", "diagonal", "grid", "perimeter")
STYLE_LABELS = {
    "spine": "дорожки от подъезда к подъезду",
    "diagonal": "площадь на пересечении диагональных дорожек",
    "grid": "сетка дорожек",
    "perimeter": "дорожка по периметру двора",
}

BUILDING_CLEARANCE_M = 2.0  # место под дизайн -- не ближе к стенам
BLOCK_CLEARANCE_M = 1.0  # и к парковкам, площадкам, дорогам, границе участка
MIN_DESIGN_AREA_M2 = 150.0
# Без указанной точки area() берёт крупнейший свободный кусок участка целиком;
# на районе (locations/05, 2 км²) это весь газон -- расчёт стал бы неоправданно
# тяжёлым. Дизайн крупнее обычного двора обрезаем кругом вокруг его "глубокой"
# точки (polylabel), а не отклоняем: пользователь без указанного места всё
# равно должен получить связный результат, просто на части территории.
MAX_DESIGN_AREA_M2 = 40000.0
ENTRANCE_REACH_M = 14.0  # подъезд "выходит" в эту часть двора
ENCLOSURE_SAMPLE_STEP_M = 3.0  # шаг вдоль контура при проверке окружения зданиями
ENCLOSURE_REACH_M = 12.0  # точка контура "смотрит" на здание, если оно не дальше
ENCLOSURE_MIN_RATIO = 0.2  # доля контура, обращённая к зданиям -- чтобы считать двором
ENCLOSURE_MIN_BUILDINGS = 2  # "между домами" -- значит домов не меньше двух
# Не поведенческий лимит (двор с бОльшим числом подъездов -- редкость), а
# защита от вырожденного случая: design_area без точки на огромном участке
# уже обрезаётся по MAX_DESIGN_AREA_M2, но подъездов внутри обрезанного круга
# всё ещё может быть много.
MAX_ENTRANCE_LINKS = 60
ENTRANCE_LINK_MIN_M = 1.5  # ближе -- подъезд и так у сети, отдельный отрезок не нужен
ENTRANCE_LINK_LEAK_M = 0.3  # сколько отрезка допустимо вне мостимой области (округления)
PLAZA_MIN_DEPTH_M = 8.0  # площадь у узла дерева -- только если до края двора не меньше
SPINE_HUB_MIN_USES = 2  # площадь на "spine" -- только если в узле реально сходится 2+ дорожки
PATH_HALF_WIDTH_M = 0.6  # сегмент дорожки 2 x 1.2 м
GRID_SPACING_TARGET_M = 16.0  # шаг сетки -- не мельче, но не больше 5 линий на сторону
GRID_MAX_LINES_PER_AXIS = 5
GRID_MIN_AREA_M2 = 3 * MIN_DESIGN_AREA_M2  # сетка -- только на просторном дворе
GRID_ASPECT_RATIO = 2.0  # вытянутый двор (длинная сторона / короткая)
FACING_SIGHT_MARGIN_M = 0.5  # у самой двери прямая неизбежно задевает свою же стену
ANCHOR_OFFSET_M = 3.0  # насколько от подъезда вглубь двора выносится узел магистрали
PARKING_LINK_REACH_M = 30.0  # выход на парковку -- только если сеть дорожек не дальше

# Шаг вдоль дорожки и смещение центра элемента от её оси.
LAMP_STEP_M, LAMP_OFFSET_M = 14.0, 1.4
BENCH_STEP_M, BENCH_OFFSET_M = 12.0, 1.5
# Деревья -- разброс по свободной площади двора, а не вдоль дорожки (см.
# scatter() и его докстринг): плотность посадки и минимальные размеры группы.
TREE_SCATTER_AREA_PER_TREE_M2 = 220.0
TREE_SCATTER_MIN_COUNT = 3
TREE_SCATTER_MAX_COUNT = 30
BUSH_STEP_M, BUSH_OFFSET_M = 3.0, 1.6
TRASH_SHIFT_M = 1.3  # урна -- рядом со скамейкой, вдоль дорожки
ENTRANCE_BED_BACK_M, ENTRANCE_BED_OFFSET_M = 3.5, 2.4
HEDGE_INSET_M = 1.2
HEDGE_FACADE_CLEARANCE_M = 5.0  # вдоль фасадов изгородь не ставим
# Минимум от оси любой новой дорожки до центра элемента -- чтобы не на мощении.
AXIS_CLEARANCE_M = {
    "lamp": 1.0,
    "bench": 1.1,
    "trash": 1.0,
    "flowerbed_patch": 1.9,
    "fountain": 1.9,
    "hedge_segment": 1.4,
    "tree": 2.4,
    "bush": 1.2,
}

Create = Callable[[CatalogItem, float, float, float], None]


def _lines(geom) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [LineString(geom.coords)]
    return [part for g in getattr(geom, "geoms", []) for part in _lines(g)]


def _rotation(tx: float, tz: float) -> float:
    # Как в Placer.points_along: поворот θ кладёт локальную ось X по (cosθ, -sinθ).
    return math.degrees(math.atan2(-tz, tx)) if (tx or tz) else 0.0


def _samples(line: LineString, step: float):
    """(x, z, tx, tz) через step вдоль линии, с отступом от концов поровну;
    (tx, tz) -- единичная касательная."""
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


def _footprint(item: CatalogItem, x: float, z: float, rotation_deg: float):
    """Полный габарит объекта в плане -- прямоугольник (лавка, секция
    изгороди, клумба, дорожка) или круг (дерево, куст, фонарь, урна, фонтан),
    а не одна точка-центр. Раньше проверялась только точка постановки: объект
    мог стоять "правильно" по центру, а его дальний край всё равно перекрывал
    дорожку или высовывался за границу двора."""
    dims = item.dimensions
    if dims.width and dims.depth:
        angle = math.radians(rotation_deg)
        ux, uz = math.cos(angle), -math.sin(angle)  # ось X объекта (как в _rotation)
        vx, vz = math.sin(angle), math.cos(angle)  # перпендикулярная ось Z объекта
        hw, hd = dims.width / 2, dims.depth / 2
        return Polygon(
            [
                (x + ux * hw + vx * hd, z + uz * hw + vz * hd),
                (x - ux * hw + vx * hd, z - uz * hw + vz * hd),
                (x - ux * hw - vx * hd, z - uz * hw - vz * hd),
                (x + ux * hw - vx * hd, z + uz * hw - vz * hd),
            ]
        )
    if dims.radius:
        return Point(x, z).buffer(dims.radius, quad_segs=8)
    return Point(x, z)


def _rect_sides(poly) -> list[tuple[float, float, float]]:
    """Две смежные стороны минимального описанного прямоугольника, как
    (единичный вектор x, единичный вектор z, длина стороны)."""
    coords = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
    sides = []
    for i in range(2):
        (ax, az), (bx, bz) = coords[i], coords[i + 1]
        length = math.hypot(bx - ax, bz - az) or 1.0
        sides.append(((bx - ax) / length, (bz - az) / length, length))
    return sides


def _mst_edges(points: list[Point]) -> list[tuple[int, int]]:
    """Индексы рёбер минимального остовного дерева (алгоритм Прима) по прямой
    евклидовой метрике -- связывает набор точек кратчайшей суммой отрезков,
    не проверяя, что каждый отрезок реально проходим (это отдельная проверка
    у вызывающего кода). Нужен, чтобы дорожки соединяли подъезды напрямую, а
    не образовывали произвольную геометрическую фигуру: см. _network_spine."""
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    dist = [math.inf] * n
    parent = [-1] * n
    dist[0] = 0.0
    edges = []
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: dist[i])
        in_tree[u] = True
        if parent[u] != -1:
            edges.append((parent[u], u))
        for v in range(n):
            if not in_tree[v]:
                d = points[u].distance(points[v])
                if d < dist[v]:
                    dist[v] = d
                    parent[v] = u
    return edges


def _main_directions(poly) -> list[tuple[float, float]]:
    """Единичные векторы вдоль сторон минимального описанного прямоугольника --
    главные оси двора, в обе стороны."""
    directions = []
    for ux, uz, _ in _rect_sides(poly):
        directions += [(ux, uz), (-ux, -uz)]
    return directions


class CourtyardDesigner:
    def __init__(self, scene: Scene, placer: Placer, create: Create):
        self.placer = placer
        self.create = create
        self.counts: Counter[str] = Counter()
        self.axes: list[LineString] = []
        self.path_length = 0.0
        self._axes_union = None
        self.uncapped_area_m2: Optional[float] = None  # исходная площадь, если area() её обрезала
        self.unreached_entrances = 0  # подъезды, для которых не нашлось дорожки без нарушений норм
        self.entrances_considered = 0  # подъезды двора -- знаменатель для unreached_entrances
        self.entrances = [Point(o.position.x, o.position.z) for o in scene.objects if o.type == "entrance"]

        buildings = [geom for zone, geom in placer.zones if zone.type == "building"]
        blocked = [
            geom
            for zone, geom in placer.zones
            if zone.type in ("road", "playground_zone", "transformer") or "PARK" in zone.name.upper()
        ]
        # Существующие тротуары (из DXF) -- не запрет для НОВОЙ дорожки по
        # нормам, но прокладывать новую поверх уже существующей бессмысленно
        # и на плане выглядит как нелепое дублирование: тот участок пути уже
        # обслужен. Однако сам подъезд нередко стоит впритык именно к такому
        # тротуару (вход и так выходит на готовую дорожку) -- маршрут к сети
        # ДОЛЖЕН иметь право пройти рядом с ним или по нему, просто не класть
        # там новую плитку поверх старой. Поэтому это разные области:
        # self.routable -- где вообще можно провести дорожку (для проверки
        # проходимости стыков и рёбер дерева), self.paving -- где реально
        # укладывается новая плитка (уже -- без существующих тротуаров).
        existing_paths = [geom for zone, geom in placer.zones if zone.type == "pedestrian_path"]
        self.buildings = buildings  # отдельные контуры -- для проверки "между домами" (_enclosure)
        self.facades = unary_union(buildings) if buildings else None
        self.routable = None  # где вообще может пройти дорожка (существующие тротуары не преграда)
        self.paving = None  # где реально укладывается НОВАЯ плитка
        self.free = None  # где можно разместить дизайн
        if placer.site is not None:
            self.routable = placer.site.buffer(-PATH_HALF_WIDTH_M)
            if buildings or blocked:
                self.routable = self.routable.difference(unary_union(buildings + blocked))
            self.paving = self.routable.difference(unary_union(existing_paths)) if existing_paths else self.routable
            self.free = placer.site.buffer(-BLOCK_CLEARANCE_M)
            if buildings or blocked:
                cut = [g.buffer(BUILDING_CLEARANCE_M) for g in buildings] + [g.buffer(BLOCK_CLEARANCE_M) for g in blocked]
                self.free = self.free.difference(unary_union(cut))

    # --- Место ---------------------------------------------------------------

    def _enclosure(self, poly) -> tuple[float, int]:
        """(доля контура, обращённая к зданиям; сколько разных зданий рядом).
        "Двор" -- пространство между домами, а не любой свободный газон: узкая
        полоса вдоль внешнего края участка касается от силы одного здания, а
        настоящий двор смотрит стенами с нескольких сторон."""
        if not self.buildings:
            return 0.0, 0
        boundary = poly.exterior
        length = boundary.length
        if length <= 0:
            return 0.0, 0
        samples = max(int(length // ENCLOSURE_SAMPLE_STEP_M), 8)
        facing = 0
        near_buildings: set[int] = set()
        for i in range(samples):
            p = boundary.interpolate(i * length / samples)
            for bi, building in enumerate(self.buildings):
                if building.distance(p) <= ENCLOSURE_REACH_M:
                    facing += 1
                    near_buildings.add(bi)
                    break
        return facing / samples, len(near_buildings)

    def area(self, x: Optional[float], z: Optional[float], radius: float):
        if self.free is None:
            return None
        free = self.free if x is None or z is None else self.free.intersection(Point(x, z).buffer(radius))
        parts = [p for p in polygons(free) if p.area >= MIN_DESIGN_AREA_M2]
        if not parts:
            return None

        def score(poly):
            ratio, near_buildings = self._enclosure(poly)
            is_courtyard = ratio >= ENCLOSURE_MIN_RATIO and near_buildings >= ENCLOSURE_MIN_BUILDINGS
            near_entrances = sum(1 for e in self.entrances if poly.distance(e) <= ENTRANCE_REACH_M)
            return is_courtyard, near_entrances > 0, poly.area * (1 + near_entrances)

        best = max(parts, key=score)
        self.uncapped_area_m2 = best.area if best.area > MAX_DESIGN_AREA_M2 else None
        if self.uncapped_area_m2 is not None:
            center = polylabel(best, tolerance=1.0)
            capped = best.intersection(center.buffer(math.sqrt(MAX_DESIGN_AREA_M2 / math.pi)))
            best = max(polygons(capped), key=lambda p: p.area, default=best)
        return best

    # --- Шаблоны каркаса дорожек ---------------------------------------------
    #
    # Каждый строит "скелет" -- дорожки без связей с подъездами -- и возвращает
    # (скелет, точку-центр для клумб/фонтана, радиус площади или 0, если её
    # по шаблону нет). Связи с подъездами и запасные лучи -- общий шаг ниже.

    def _entrance_anchor(self, poly, entrance: Point) -> Point:
        """Точка на ANCHOR_OFFSET_M вглубь двора от подъезда, по нормали к
        границе места. Прямая МЕЖДУ ДВУМЯ ПОДЪЕЗДАМИ одной стены идёт вдоль
        фасада и совпадает с ним -- она вообще не заходит в открытую часть
        двора (это и оказалось причиной, почему первая версия "дерева
        подъездов" не давала ни одной дорожки: у здания без изломов все рёбра
        MST шли точно по стене). Магистраль поэтому строится между анкерами --
        вынесенными в глубину точками, а от двери до анкера идёт короткий
        прямой подход."""
        near = nearest_points(entrance, poly)[1]
        dx, dz = near.x - entrance.x, near.y - entrance.y
        dist = math.hypot(dx, dz) or 1.0
        reach = dist + ANCHOR_OFFSET_M
        return Point(entrance.x + dx / dist * reach, entrance.y + dz / dist * reach)

    def _network_spine(self, poly, near_entrances: list[Point]) -> list:
        """Дорожки от подъезда к подъезду -- как в реальном дворе -- вместо
        произвольной геометрической фигуры: короткий подход от каждой двери к
        своему анкеру (см. _entrance_anchor) плюс минимальное дерево
        (_mst_edges) между анкерами -- это и есть магистраль, идущая вдоль
        двора на разумном расстоянии от фасадов, а не повторяющая их линию.
        Ребро дерева, которое всё же упёрлось в препятствие, просто
        отбрасывается: его подъезды всё равно свяжутся с сетью на общем шаге
        ниже (через центр двора), так что чинить каждое перекрытие здесь не
        нужно."""
        if not near_entrances:
            return []
        inner = poly.buffer(-1.0)
        anchors = [self._entrance_anchor(poly, e) for e in near_entrances]
        segments = []
        for entrance, anchor in zip(near_entrances, anchors):
            stub = LineString([(entrance.x, entrance.y), (anchor.x, anchor.y)]).intersection(inner)
            segments.extend(p for p in _lines(stub) if p.length >= 0.5)
        for i, j in _mst_edges(anchors):
            edge = LineString([(anchors[i].x, anchors[i].y), (anchors[j].x, anchors[j].y)])
            clipped = edge.intersection(inner)
            covered = clipped.length if not clipped.is_empty else 0.0
            if edge.length - covered > ENTRANCE_LINK_LEAK_M:
                continue
            segments.extend(p for p in _lines(clipped) if p.length >= 0.5)
        return segments

    def _network_diagonal(self, poly, depth: float, center: Point):
        plaza = min(max(0.25 * depth, 3.0), 6.0) if depth >= PLAZA_MIN_DEPTH_M else 0.0
        inner = poly.buffer(-2.5)
        corners = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
        network = []
        for a, b in ((corners[0], corners[2]), (corners[1], corners[3])):
            piece = LineString([a, b]).intersection(inner)
            network += [g for g in _lines(piece) if g.length >= 4.0]
        if plaza:
            network.append(center.buffer(plaza, quad_segs=12).exterior)
        return network, plaza

    def _network_grid(self, poly, center: Point):
        inner = poly.buffer(-2.5)
        if inner.is_empty:
            return [], 0.0
        network = []
        for ux, uz, length in _rect_sides(poly):
            perp_x, perp_z = -uz, ux  # направление, вдоль которого раскладываем линии сетки
            lines_count = min(GRID_MAX_LINES_PER_AXIS, max(int(length // GRID_SPACING_TARGET_M), 1))
            spacing = length / (lines_count + 1)
            far = max(length, 10.0)
            for k in range(1, lines_count + 1):
                offset = -length / 2 + k * spacing
                sx, sz = center.x + perp_x * offset, center.y + perp_z * offset
                ray = LineString([(sx - ux * far, sz - uz * far), (sx + ux * far, sz + uz * far)])
                network += [g for g in _lines(ray.intersection(inner)) if g.length >= 4.0]
        return network, 0.0

    def _network_perimeter(self, poly, depth: float, center: Point):
        inset = min(max(depth * 0.5, 2.0), 6.0)
        loops = sorted(polygons(poly.buffer(-inset)), key=lambda p: -p.area)
        return ([loops[0].exterior] if loops else []), 0.0

    def _choose_style(self, poly, depth: float, near_entrances: list) -> str:
        """"diagonal" сюда намеренно не входит -- крест по диагоналям красив,
        но не то, что появляется во дворе само по себе; его выбирают явно."""
        (_, _, side_a), (_, _, side_b) = _rect_sides(poly)
        aspect = max(side_a, side_b) / max(min(side_a, side_b), 1e-6)
        if depth < PLAZA_MIN_DEPTH_M:
            return "perimeter"
        if not near_entrances:
            # Нечего соединять "от подъезда к подъезду" -- открытая площадка
            # без входов рядом, сетка дорожек уместнее произвольной фигуры.
            return "grid" if poly.area >= GRID_MIN_AREA_M2 else "perimeter"
        if aspect >= GRID_ASPECT_RATIO and poly.area >= GRID_MIN_AREA_M2:
            return "grid"
        return "spine"

    def _faces_courtyard(self, poly, entrance: Point) -> bool:
        """Подъезд действительно выходит в эту часть двора, а не смотрит на
        улицу с противоположной стороны того же (обычно тонкого) корпуса.
        Проверяем не расстояние (щедрый ENTRANCE_REACH_M его не различает), а
        видимость: прямая от двери до ближайшей точки области не должна
        проходить сквозь стену -- уличный подъезд эта прямая неизбежно
        пересечёт собственное здание."""
        if self.facades is None:
            return True
        near = nearest_points(entrance, poly)[1]
        sight = LineString([(entrance.x, entrance.y), (near.x, near.y)])
        if sight.length <= FACING_SIGHT_MARGIN_M:
            return True
        # Первые FACING_SIGHT_MARGIN_M от двери не считаем -- там линия
        # неизбежно задевает стену, у которой сам подъезд стоит.
        trimmed = LineString([sight.interpolate(FACING_SIGHT_MARGIN_M), sight.interpolate(sight.length)])
        return not trimmed.intersects(self.facades)

    def _link_entrance(self, network: list, entrance: Point, center: Point) -> tuple[Optional[LineString], bool]:
        """(отрезок от сети к подъезду, использован ли для этого центр двора
        как самостоятельный узел). Отрезок -- пустая линия, если подъезд и
        так у сети, None -- если дороги без нарушений не провести. Пробуем не
        только глобально ближайшую точку сети (её может закрывать угол
        соседнего здания или уже поставленная клумба), а точку на каждом
        куске сети по очереди, от ближайшего к дальнему, и отдельно сам центр
        двора: одно перекрытие не должно оставлять подъезд вовсе без дорожки."""
        anchors = [*network, center]
        for anchor in sorted(anchors, key=lambda g: g.distance(entrance)):
            # nearest_points(a, b) возвращает (точка на a, точка на b); нужна
            # первая -- ближайшая точка НА СЕТИ, а не сама точка подъезда,
            # которую вернуло бы `_, near = ...` (раньше так и было: near
            # оказывался entrance-ом, линия -- нулевой длины, и любой подъезд
            # считался "уже у сети", хотя дорожка к нему не доходила).
            near, _ = nearest_points(anchor, entrance)
            link = LineString([(near.x, near.y), (entrance.x, entrance.y)])
            used_hub = anchor is center
            if link.length < ENTRANCE_LINK_MIN_M:
                return LineString(), used_hub
            # Проходимость -- по routable (существующий тротуар не мешает
            # маршруту, подъезд может стоять впритык к нему); реальная
            # плитка на этом отрезке появится только там, где routable и
            # paving совпадают -- см. pave().
            if link.difference(self.routable).length <= ENTRANCE_LINK_LEAK_M:
                return link, used_hub
        return None, False

    def _fit_plaza(self, network: list, max_radius: float) -> Optional[tuple[Point, float]]:
        """(точка, радиус) площади, гарантированно вписанной в допустимую
        область целиком -- не только не пересекающей здание своим центром, а
        не задевающей его габаритом круга. Перебираем несколько точек вдоль
        самых длинных уже проложенных отрезков сети (площадь без выбора
        стоит НА сети -- связь тем самым гарантирована) и, если нужно,
        уменьшаем радиус: у сети, идущей всего в паре метров от фасада (см.
        _entrance_anchor), полный радиус там попросту не поместится."""
        pieces = sorted(network, key=lambda g: -g.length)[:5]
        for radius in (max_radius, max_radius * 0.7, max_radius * 0.5, 3.0):
            for piece in pieces:
                for frac in (0.5, 0.35, 0.65):
                    point = piece.interpolate(piece.length * frac)
                    if self.placer.region_contains(point.buffer(radius, quad_segs=12), None):
                        return point, radius
        return None

    def layout(self, poly, style: str = "auto", want_fountain: bool = False):
        center = polylabel(poly, tolerance=0.5)
        depth = poly.boundary.distance(center)
        near_entrances = sorted(
            (e for e in self.entrances if poly.distance(e) <= ENTRANCE_REACH_M and self._faces_courtyard(poly, e)),
            key=center.distance,
        )

        if style not in STYLES or style == "auto":
            style = self._choose_style(poly, depth, near_entrances)
        plaza = 0.0
        if style == "diagonal":
            network, plaza = self._network_diagonal(poly, depth, center)
        elif style == "grid":
            network, plaza = self._network_grid(poly, center)
        elif style == "perimeter":
            network, plaza = self._network_perimeter(poly, depth, center)
        else:
            style = "spine"
            network = self._network_spine(poly, near_entrances)

        # От каждого подъезда, что выходит в эту часть двора, должна идти
        # дорожка -- дальние подъезды цепляются через уже проложенные к
        # ближним отрезки, а не только через центр.
        links = []
        unreached = 0
        hub_uses = 0
        considered = near_entrances[:MAX_ENTRANCE_LINKS]
        for entrance in considered:
            link, used_hub = self._link_entrance(network, entrance, center)
            if link is None:
                unreached += 1
                continue
            hub_uses += used_hub
            if not link.is_empty:
                network.append(link)
                links.append(link)
        self.unreached_entrances = unreached
        self.entrances_considered = len(considered)

        # Площадь на "spine" -- не по умолчанию (см. докстринг модуля: не в
        # каждом дворе она уместна), а только когда для неё есть конкретный
        # повод: явно просят фонтан (ему нужна прогалина) или несколько
        # дорожек и так уже сошлись в центр двора как в запасной узел
        # (hub_uses) -- то есть площадь тут появилась бы сама по себе, а не
        # декоративно навязана.
        if style == "spine" and not plaza and depth >= PLAZA_MIN_DEPTH_M and (want_fountain or hub_uses >= SPINE_HUB_MIN_USES):
            target_radius = min(max(0.3 * depth, 4.0), 8.0)
            plaza_center = center
            fits_at_center = self.placer.region_contains(center.buffer(target_radius, quad_segs=12), None)
            if fits_at_center and network and not hub_uses:
                # Место у центра есть, но дорожки сами к нему не вышли
                # (hub_uses == 0, площадь вызвана только просьбой про фонтан)
                # -- без явной связи она осталась бы островом посреди двора.
                nearest_piece = min(network, key=lambda g: g.distance(center))
                near, _ = nearest_points(nearest_piece, center)
                stub = LineString([(near.x, near.y), (center.x, center.y)])
                if stub.length >= ENTRANCE_LINK_MIN_M:
                    if stub.difference(self.routable).length <= ENTRANCE_LINK_LEAK_M:
                        network.append(stub)
                    else:
                        fits_at_center = False  # дойти по прямой нельзя -- ищем место на самой сети
            if not fits_at_center:
                # "Глубочайшая" точка двора (center) в изломанном дворе
                # бывает физически не видна с сети напрямую (спрятана за
                # углом здания) или сама площадь там не влезает целиком --
                # ищем место НА уже проложенной сети (связь тогда
                # гарантирована самим построением), с фактически
                # вписывающимся радиусом.
                fit = self._fit_plaza(network, target_radius) if network else None
                target_radius = 0.0 if fit is None else fit[1]
                plaza_center = center if fit is None else fit[0]
            plaza = target_radius
            if plaza:
                center = plaza_center
                network.append(center.buffer(plaza, quad_segs=12).exterior)

        if not network:
            # Ни площади, ни одного подъезда рядом -- лучи по главным осям
            # двора, чтобы дорожки не пропали вовсе.
            inner = poly.buffer(-3.0)
            for dx, dz in _main_directions(poly):
                ray = LineString([(center.x, center.y), (center.x + dx * 2000, center.y + dz * 2000)])
                piece = next((g for g in _lines(ray.intersection(inner)) if g.distance(center) < 0.5), None)
                if piece is not None and piece.length >= 3.0:
                    network.append(piece)

        # Выход на парковку -- та же логика, что и подъезды: если парковка
        # рядом, дорожка сама к ней подходит, а не остаётся сетью "в себе".
        # Не элемент по выбору -- в реальном дворе путь к местам для машин
        # нужен почти всегда, если парковка вообще есть поблизости.
        if network:
            parking = self.placer.target_geometry("parking")
            if parking is not None and unary_union(network).distance(parking) <= PARKING_LINK_REACH_M:
                near_net, near_parking = nearest_points(unary_union(network), parking)
                link = LineString([(near_net.x, near_net.y), (near_parking.x, near_parking.y)])
                if link.length >= ENTRANCE_LINK_MIN_M and link.difference(self.routable).length <= ENTRANCE_LINK_LEAK_M:
                    network.append(link)
        return center, plaza, network, links, style

    def pave(self, network: list, item: CatalogItem) -> None:
        pieces = [g for line in network for g in _lines(line.intersection(self.paving)) if g.length >= 1.0]
        for piece in pieces:
            for x, z, tx, tz in _samples(piece, item.dimensions.width or 2.0):
                self.create(item, x, z, _rotation(tx, tz))
                self.counts[item.object_type] += 1
        self.axes = pieces
        self.path_length = sum(p.length for p in pieces)
        self._axes_union = unary_union(pieces) if pieces else None

    # --- Детали --------------------------------------------------------------

    def _axis_distance(self, shape) -> float:
        return math.inf if self._axes_union is None else self._axes_union.distance(shape)

    def _put(self, item: CatalogItem, x: float, z: float, rotation: float = 0.0, on_axis: bool = False) -> bool:
        """Разместить, проверив ВЕСЬ габарит объекта (см. _footprint), а не
        только точку постановки: лавка длиной 1.4 м или секция изгороди 2 м,
        проверенные по одной точке-центру, могли на треть повиснуть над
        дорожкой или уйти за границу двора, хотя сама точка стояла верно.
        on_axis -- для фонтана и центральной клумбы площади: они стоят ровно
        в узле, где дорожки закономерно сходятся (расстояние до оси -- 0 по
        замыслу), поэтому обычная проверка AXIS_CLEARANCE_M (не ближе к
        дорожке, идущей МИМО) для них неприменима."""
        kind = item.setback_kind
        shape = _footprint(item, x, z, rotation)
        if not self.placer.region_contains(shape, kind):
            return False
        if not on_axis and self._axis_distance(shape) < AXIS_CLEARANCE_M.get(item.object_type, 1.0):
            return False
        if self.placer.blocker(x, z, kind, obj_type=item.object_type) is not None:
            return False
        self.create(item, x, z, rotation)
        self.counts[item.object_type] += 1
        return True

    def along(
        self,
        items: list[CatalogItem],
        step: float,
        offset: float,
        both_sides: bool,
        oriented: bool = False,
        companion: Optional[CatalogItem] = None,
    ):
        """Элементы вдоль всех новых дорожек: с обеих сторон или зигзагом.
        companion -- напарник у КАЖДОГО элемента (урна у каждой лавки: стандартная
        практика площадок отдыха -- "pair cans with benches to reduce litter",
        https://www.keystoneridgedesigns.com/blog/2017/03/Site-Furniture-Placement-Guidelines.aspx),
        а не у каждого второго; ставится рядом вдоль той же дорожки и не
        мешает основному элементу состояться, если сам не помещается."""
        placed = []
        index = 0
        for piece in self.axes:
            for x, z, tx, tz in _samples(piece, step):
                sides = (1.0, -1.0) if both_sides else (1.0 if index % 2 == 0 else -1.0,)
                for side in sides:
                    item = items[index % len(items)]
                    index += 1
                    px, pz = x - tz * offset * side, z + tx * offset * side
                    rotation = _rotation(tx, tz) if oriented else (index * 137.5) % 360
                    if self._put(item, px, pz, rotation):
                        placed.append((px, pz, tx, tz))
                        if companion is not None:
                            self._put(companion, px + tx * TRASH_SHIFT_M, pz + tz * TRASH_SHIFT_M, rotation)
        return placed

    def flowerbeds(self, center: Point, plaza: float, links: list, bed: CatalogItem, fountain: bool) -> None:
        if plaza:
            if not fountain:
                self._put(bed, center.x, center.y, on_axis=True)
            radius = max(0.55 * plaza, 2.8 if fountain else 2.6)
            count = 6 if plaza >= 7 else 4
            for k in range(count):
                angle = 2 * math.pi * k / count + math.pi / count
                x, z = center.x + radius * math.cos(angle), center.y + radius * math.sin(angle)
                self._put(bed, x, z, _rotation(-math.sin(angle), math.cos(angle)))
        for link in links:
            if link.length < ENTRANCE_BED_BACK_M + 1.0:
                continue
            p = link.interpolate(link.length - ENTRANCE_BED_BACK_M)
            (ax, az), (bx, bz) = link.coords[0], link.coords[-1]
            tx, tz = (bx - ax) / link.length, (bz - az) / link.length
            for side in (1.0, -1.0):
                offset = ENTRANCE_BED_OFFSET_M * side
                self._put(bed, p.x - tz * offset, p.y + tx * offset, _rotation(tx, tz))

    def hedge(self, poly, item: CatalogItem) -> None:
        width = item.dimensions.width or 2.0
        for part in polygons(poly.buffer(-HEDGE_INSET_M)):
            for x, z, tx, tz in _samples(part.exterior, width):
                if self.facades is not None and self.facades.distance(Point(x, z)) < HEDGE_FACADE_CLEARANCE_M:
                    continue
                self._put(item, x, z, _rotation(tx, tz))

    def scatter(self, poly, items: list[CatalogItem]) -> int:
        """count объектов (по площади poly, см. TREE_SCATTER_*), равномерно
        разбросанных по свободной площади двора, -- вместо ряда вдоль
        дорожки. Для дерева (отступ от здания -- 5 м) дорожка, идущая всего в
        3 м от подъезда (см. _entrance_anchor), для придорожной посадки уже
        слишком близко почти на всём протяжении: в along() с обеих сторон
        одна сторона систематически ближе к стене, чем норма позволяет,
        поэтому "деревья вдоль дорожек" в дизайне двора почти всегда
        отклонялись целиком. Разброс по площади работает в любом дворе, а не
        только там, где путь идёт с запасом от стен по обе стороны -- это же
        и есть простая схема "деревья внутри", а не вдоль периметра."""
        if not items:
            return 0
        kind = items[0].setback_kind
        # Крона, а не ствол: candidate с центром ровно на границе нормы имеет
        # крону radius метров в поперечнике, и она вылезала бы за эту границу,
        # если проверять только точку (см. _put()/_footprint -- тот же самый
        # разбор для лавок и изгороди чуть раньше в этом файле). Используем
        # САМЫЙ крупный вид из пула, чтобы проверка годилась для любого из
        # них, кто бы ни занял эту точку при чередовании.
        biggest = max(items, key=lambda i: i.dimensions.radius or 0.0)
        radius = biggest.dimensions.radius or 1.0
        spacing = max(5.0, 2 * radius + 2.0)
        count = int(clamp(round(poly.area / TREE_SCATTER_AREA_PER_TREE_M2), TREE_SCATTER_MIN_COUNT, TREE_SCATTER_MAX_COUNT))
        axis_clearance = AXIS_CLEARANCE_M.get(biggest.object_type, 1.0)

        def valid(x: float, z: float) -> bool:
            shape = _footprint(biggest, x, z, 0.0)
            return (
                self.placer.region_contains(shape, kind)
                and self._axis_distance(shape) >= axis_clearance
                and self.placer.blocker(x, z, kind, obj_type=biggest.object_type) is None
            )

        candidates = self.placer.points_in_area(kind, spacing / 2, poly, max_candidates=count * 20)
        free = [p for p in candidates if valid(*p)]
        chosen = pick_spread(free, count, 0.9 * spacing)
        for i, (x, z) in enumerate(chosen):
            item = items[i % len(items)]
            self.create(item, x, z, (i * 137.5) % 360)
            self.counts[item.object_type] += 1
        return len(chosen)

    # --- Всё вместе ----------------------------------------------------------

    def run(
        self,
        elements: list[str],
        items: dict[str, CatalogItem],
        trees: list[CatalogItem],
        bushes: list[CatalogItem],
        x: Optional[float] = None,
        z: Optional[float] = None,
        radius: float = 40.0,
        style: str = "auto",
    ) -> tuple[Optional[str], list[str]]:
        """(сводка или None, если дизайн не поместился; замечания)."""
        poly = self.area(x, z, radius)
        if poly is None:
            return None, [f"нет связного свободного места под дизайн (нужно от {MIN_DESIGN_AREA_M2:.0f} м²)"]
        center, plaza, network, links, style_used = self.layout(poly, style, want_fountain="fountain" in elements)
        # Порядок -- от главного к второстепенному: каждый следующий элемент
        # обходит уже поставленные.
        self.pave(network, items["path_segment"])
        notes = []
        if self.uncapped_area_m2 is not None:
            notes.append(
                f"свободного места {self.uncapped_area_m2:.0f} м² — больше обычного двора, "
                f"дизайн выполнен на части площадью {poly.area:.0f} м² вокруг центра; "
                "укажите точку и радиус, если нужна другая часть"
            )
        if self.unreached_entrances:
            notes.append(
                f"для {self.unreached_entrances} из {self.entrances_considered} подъездов дорожку "
                "без нарушений норм провести не удалось (мешают соседние здания или зоны)"
            )
        fountain = False
        if "fountain" in elements:
            if plaza:
                fountain = self._put(items["fountain"], center.x, center.y, on_axis=True)
            else:
                notes.append("для этого шаблона дорожек площадь с фонтаном не предусмотрена")
        if "flowerbeds" in elements:
            self.flowerbeds(center, plaza, links, items["flowerbed_patch"], fountain)
        if "lamps" in elements:
            self.along([items["lamp"]], LAMP_STEP_M, LAMP_OFFSET_M, both_sides=False)
        if "benches" in elements:
            companion = items["trash"] if "trash" in elements else None
            self.along([items["bench"]], BENCH_STEP_M, BENCH_OFFSET_M, both_sides=False, oriented=True, companion=companion)
        elif "trash" in elements:
            self.along([items["trash"]], 3 * BENCH_STEP_M, BENCH_OFFSET_M, both_sides=False)
        if "hedge" in elements:
            self.hedge(poly, items["hedge_segment"])
        if "trees" in elements and trees:
            self.scatter(poly, trees)
        if "bushes" in elements and bushes:
            self.along(bushes, BUSH_STEP_M, BUSH_OFFSET_M, both_sides=True)

        parts = [f"шаблон «{STYLE_LABELS[style_used]}»", f"место {poly.area:.0f} м²", f"дорожки {self.path_length:.0f} м"]
        missing = []
        for element in elements:
            if element == "paths":
                continue
            count = self.counts[_ELEMENT_TYPES[element]]
            if count:
                parts.append(f"{ELEMENT_LABELS[element]} — {count}")
            elif element != "fountain" or plaza:
                missing.append(ELEMENT_LABELS[element])
        if missing:
            notes.append(f"не нашлось места без нарушений норм: {', '.join(missing)}")
        return ", ".join(parts), notes
