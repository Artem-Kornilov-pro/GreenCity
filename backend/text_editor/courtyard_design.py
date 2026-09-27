"""
Полный дизайн двора для правки текстом (операция design_area в text_editor/service.py).

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

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from core.placement import Placer, clamp, polygons
from core.placement_geometry import lines_of, pick_spread, rotation_of, sample_line
from core.plant_catalog import CatalogItem
from core.schemas import Scene
from text_editor.courtyard_layout import (
    MIN_DESIGN_AREA_M2,
    STYLE_LABELS,
    CourtyardLayoutMixin,
)

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
# Площадь с фонтаном: дорожки обрываются на столько раньше её кольца --
# само кольцо (мощение по краю площади) остаётся.
PLAZA_RING_KEEP_M = 1.0

DESIGN_ITEM_IDS = ("path_segment", "flowerbed_patch", "fountain", "lamp", "bench", "trash", "hedge_segment")


BUILDING_CLEARANCE_M = 2.0  # место под дизайн -- не ближе к стенам


BLOCK_CLEARANCE_M = 1.0  # и к парковкам, площадкам, дорогам, границе участка


PATH_HALF_WIDTH_M = 0.6  # сегмент дорожки 2 x 1.2 м


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


def _footprint(item: CatalogItem, x: float, z: float, rotation_deg: float):
    """Полный габарит объекта в плане -- прямоугольник (лавка, секция
    изгороди, клумба, дорожка) или круг (дерево, куст, фонарь, урна, фонтан),
    а не одна точка-центр. Раньше проверялась только точка постановки: объект
    мог стоять "правильно" по центру, а его дальний край всё равно перекрывал
    дорожку или высовывался за границу двора."""
    dims = item.dimensions
    if dims.width and dims.depth:
        angle = math.radians(rotation_deg)
        ux, uz = math.cos(angle), -math.sin(angle)  # ось X объекта (как в rotation_of)
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


class CourtyardDesigner(CourtyardLayoutMixin):
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

    def pave(self, network: list, item: CatalogItem) -> None:
        pieces = [g for line in network for g in lines_of(line.intersection(self.paving)) if g.length >= 1.0]
        for piece in pieces:
            for x, z, tx, tz in sample_line(piece, item.dimensions.width or 2.0):
                self.create(item, x, z, rotation_of(tx, tz))
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
        if not self.placer.region_contains(shape, kind, item.label):
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
            for x, z, tx, tz in sample_line(piece, step):
                sides = (1.0, -1.0) if both_sides else (1.0 if index % 2 == 0 else -1.0,)
                for side in sides:
                    item = items[index % len(items)]
                    index += 1
                    px, pz = x - tz * offset * side, z + tx * offset * side
                    rotation = rotation_of(tx, tz) if oriented else (index * 137.5) % 360
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
                self._put(bed, x, z, rotation_of(-math.sin(angle), math.cos(angle)))
        for link in links:
            if link.length < ENTRANCE_BED_BACK_M + 1.0:
                continue
            p = link.interpolate(link.length - ENTRANCE_BED_BACK_M)
            (ax, az), (bx, bz) = link.coords[0], link.coords[-1]
            tx, tz = (bx - ax) / link.length, (bz - az) / link.length
            for side in (1.0, -1.0):
                offset = ENTRANCE_BED_OFFSET_M * side
                self._put(bed, p.x - tz * offset, p.y + tx * offset, rotation_of(tx, tz))

    def hedge(self, poly, item: CatalogItem) -> None:
        width = item.dimensions.width or 2.0
        for part in polygons(poly.buffer(-HEDGE_INSET_M)):
            for x, z, tx, tz in sample_line(part.exterior, width):
                if self.facades is not None and self.facades.distance(Point(x, z)) < HEDGE_FACADE_CLEARANCE_M:
                    continue
                self._put(item, x, z, rotation_of(tx, tz))

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
        # Отступ -- по самому строгому виду пула (липе 10 м от здания и т.п.,
        # setback_norms.py), по той же причине, что и крона самого крупного.
        species = [item.label for item in items]

        def valid(x: float, z: float) -> bool:
            shape = _footprint(biggest, x, z, 0.0)
            return (
                self.placer.region_contains(shape, kind, species)
                and self._axis_distance(shape) >= axis_clearance
                and self.placer.blocker(x, z, kind, obj_type=biggest.object_type) is None
            )

        candidates = self.placer.points_in_area(kind, spacing / 2, poly, max_candidates=count * 20, species=species)
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
        if plaza and "fountain" in elements:
            # Площадь с фонтаном: дорожки подходят к её кольцу и там
            # кончаются, середина -- свободна. Иначе у "сетки" и "креста"
            # дорожки шли прямо через центр, плитка ложилась под место
            # фонтана, и он не вставал ("не нашлось места").
            hole = center.buffer(max(plaza - PLAZA_RING_KEEP_M, 0.5))
            network = [g for line in network for g in lines_of(line.difference(hole)) if g.length >= 0.5]
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
                notes.append("фонтан: не нашлось места под площадь (круг от 4 м без сетей, дорожек и площадок)")
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
