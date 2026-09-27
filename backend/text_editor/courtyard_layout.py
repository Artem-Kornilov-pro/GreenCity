"""
Первая половина design_area (courtyard_design.py): выбор области двора и
каркас дорожек -- огорожена ли область зданиями, откуда входы, какой стиль
сети (магистраль, диагонали, сетка, периметр), где площадь в узле. Здесь нет
ни одной посадки и ни одного МАФ -- это делает CourtyardDesigner поверх
готового каркаса.
"""

from __future__ import annotations

import math
from typing import Optional

from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, polylabel, unary_union

from core.placement import polygons
from core.placement_geometry import lines_of, rect_corners, rect_sides

# Шаблоны каркаса дорожек. "auto" -- выбрать по форме двора (_choose_style);
# среди авто-кандидатов "diagonal" нет -- он декоративный, только по просьбе.
STYLES = ("auto", "spine", "diagonal", "grid", "perimeter")


STYLE_LABELS = {
    "spine": "дорожки от подъезда к подъезду",
    "diagonal": "площадь на пересечении диагональных дорожек",
    "grid": "сетка дорожек",
    "perimeter": "дорожка по периметру двора",
}


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


GRID_SPACING_TARGET_M = 16.0  # шаг сетки -- не мельче, но не больше 5 линий на сторону


GRID_MAX_LINES_PER_AXIS = 5


GRID_MIN_AREA_M2 = 3 * MIN_DESIGN_AREA_M2  # сетка -- только на просторном дворе


GRID_ASPECT_RATIO = 2.0  # вытянутый двор (длинная сторона / короткая)


FACING_SIGHT_MARGIN_M = 0.5  # у самой двери прямая неизбежно задевает свою же стену


ANCHOR_OFFSET_M = 3.0  # насколько от подъезда вглубь двора выносится узел магистрали


PARKING_LINK_REACH_M = 30.0  # выход на парковку -- только если сеть дорожек не дальше


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
    for ux, uz, _ in rect_sides(poly):
        directions += [(ux, uz), (-ux, -uz)]
    return directions


class CourtyardLayoutMixin:
    """Методы выбора области и каркаса дорожек CourtyardDesigner. Работают
    поверх атрибутов, которые задаёт CourtyardDesigner.__init__ (self.placer,
    self.buildings, self.entrances, self.routable, self.axes и т.д.) -- сам
    по себе миксин не используется."""

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
            segments.extend(p for p in lines_of(stub) if p.length >= 0.5)
        for i, j in _mst_edges(anchors):
            edge = LineString([(anchors[i].x, anchors[i].y), (anchors[j].x, anchors[j].y)])
            clipped = edge.intersection(inner)
            covered = clipped.length if not clipped.is_empty else 0.0
            if edge.length - covered > ENTRANCE_LINK_LEAK_M:
                continue
            segments.extend(p for p in lines_of(clipped) if p.length >= 0.5)
        return segments

    def _network_diagonal(self, poly, depth: float, center: Point):
        plaza = min(max(0.25 * depth, 3.0), 6.0) if depth >= PLAZA_MIN_DEPTH_M else 0.0
        inner = poly.buffer(-2.5)
        corners = rect_corners(poly)
        network = []
        for a, b in ((corners[0], corners[2]), (corners[1], corners[3])):
            piece = LineString([a, b]).intersection(inner)
            network += [g for g in lines_of(piece) if g.length >= 4.0]
        if plaza:
            network.append(center.buffer(plaza, quad_segs=12).exterior)
        return network, plaza

    def _network_grid(self, poly, center: Point):
        inner = poly.buffer(-2.5)
        if inner.is_empty:
            return [], 0.0
        network = []
        for ux, uz, length in rect_sides(poly):
            perp_x, perp_z = -uz, ux  # направление, вдоль которого раскладываем линии сетки
            lines_count = min(GRID_MAX_LINES_PER_AXIS, max(int(length // GRID_SPACING_TARGET_M), 1))
            spacing = length / (lines_count + 1)
            far = max(length, 10.0)
            for k in range(1, lines_count + 1):
                offset = -length / 2 + k * spacing
                sx, sz = center.x + perp_x * offset, center.y + perp_z * offset
                ray = LineString([(sx - ux * far, sz - uz * far), (sx + ux * far, sz + uz * far)])
                network += [g for g in lines_of(ray.intersection(inner)) if g.length >= 4.0]
        return network, 0.0

    def _network_perimeter(self, poly, depth: float, center: Point):
        inset = min(max(depth * 0.5, 2.0), 6.0)
        loops = sorted(polygons(poly.buffer(-inset)), key=lambda p: -p.area)
        return ([loops[0].exterior] if loops else []), 0.0

    def _choose_style(self, poly, depth: float, near_entrances: list) -> str:
        """"diagonal" сюда намеренно не входит -- крест по диагоналям красив,
        но не то, что появляется во дворе само по себе; его выбирают явно."""
        (_, _, side_a), (_, _, side_b) = rect_sides(poly)
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
        # Явная просьба про фонтан -- повод для площади при любом шаблоне: у
        # "сетки" и "периметра" своей площади нет, и фонтан раньше молча
        # выпадал ("для этого шаблона площадь не предусмотрена"), хотя его
        # просили прямо.
        wants_plaza = want_fountain or (style == "spine" and hub_uses >= SPINE_HUB_MIN_USES)
        if not plaza and depth >= PLAZA_MIN_DEPTH_M and wants_plaza:
            target_radius = min(max(0.3 * depth, 4.0), 8.0)
            plaza_center = center
            # Площадь поменьше в самом центре лучше, чем площадь на краю сети
            # или никакой: в пустом дворе без подъездов сети ещё нет вовсе,
            # и без этого перебора фонтан там не вставал.
            fits_at_center = False
            for radius in (target_radius, 0.7 * target_radius, 4.0):
                if radius >= 4.0 and self.placer.region_contains(center.buffer(radius, quad_segs=12), None):
                    target_radius, fits_at_center = radius, True
                    break
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
                piece = next((g for g in lines_of(ray.intersection(inner)) if g.distance(center) < 0.5), None)
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
