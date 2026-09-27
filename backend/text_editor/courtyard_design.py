"""
Дизайн двора целиком для правки текстом (операция design_area). Модель
перечисляет нужные элементы и, по желанию, стиль, а раскладку считает этот
модуль:

1. Место -- связная часть участка без зданий, парковок, площадок и дорог.
   Предпочитается та, что окружена зданиями с разных сторон, затем та, куда
   выходят подъезды, затем крупнейшая.
2. Каркас дорожек -- один из шаблонов STYLES. По умолчанию «spine»: дорожки
   от подъезда к подъезду по кратчайшим связям (desire lines); площадь --
   только там, где сходятся дорожки, или под фонтан. «grid» -- для большого
   или вытянутого двора без входов рядом, «perimeter» -- для узкого,
   «diagonal» -- только по явной просьбе. Учитываются лишь подъезды, которые
   выходят в этот двор, а не на улицу.
3. Выход к парковке поблизости.
4. Детали: фонтан (только по просьбе) и клумбы на площади, клумбы у
   подъездов, фонари зигзагом, скамейки с урнами вдоль дорожек, изгородь по
   краю двора, кусты вдоль дорожек. Деревья -- разбросом по двору, а не вдоль
   дорожек: дорожка идёт в 3 м от подъезда, а дереву нужно 5 м от стены.

Каждый элемент, кроме дорожек, проходит проверку планировщика
(placement.Placer) и не встаёт на мощение.
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


# Фонтан -- только по явной просьбе: ему нужна площадь, которая есть не в
# каждом дворе.
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
# Дорожки обрываются на столько раньше кольца площади с фонтаном.
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
    а не одна точка-центр: иначе край объекта мог перекрыть дорожку или
    выйти за границу двора."""
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
        # routable -- где можно провести дорожку (существующий тротуар не
        # мешает, подъезд часто стоит впритык к нему); paving -- где кладётся
        # новая плитка (без существующих тротуаров).
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
        """Разместить объект, проверив весь его габарит (_footprint), а не
        только точку: скамейка или секция изгороди могли повиснуть над
        дорожкой. on_axis -- для фонтана и центральной клумбы: они стоят в
        узле дорожек, и зазор до оси к ним не применяется."""
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
        """Элементы вдоль новых дорожек: с обеих сторон или зигзагом.
        companion -- напарник у каждого элемента (урна у скамейки); если он не
        помещается, основной элемент всё равно ставится."""
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
        """count объектов, равномерно разбросанных по свободной площади
        двора. Вдоль дорожек деревья почти всегда отклонялись бы: дорожка идёт
        в 3 м от подъезда, а дереву нужно 5 м от стены."""
        if not items:
            return 0
        kind = items[0].setback_kind
        # Проверяется крона самого крупного вида пула, а не точка ствола:
        # иначе крона вылезала бы за границу нормы.
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
            # Дорожки обрываются у кольца площади, середина свободна под
            # фонтан.
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
