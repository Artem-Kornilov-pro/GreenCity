"""
Благоустройство GreenPlan (параметры пользователя, greenplan/options.py):
новые дорожки во дворах, фонари, скамейки с урнами. Считается ДО посадок:
дорожки становятся зонами pedestrian_path, фонари и скамейки -- объектами
сцены, и дальше GreenPlan сажает с нормативными отступами от них (дерево --
0,7 м от дорожки и 4 м от опоры освещения, СП 42 табл. 9.1), газон их
обходит, а вдоль новых дорожек разбиение на зоны даёт полосы под изгородь.

Каркас дорожек -- тот же, что у дизайна двора в правке текстом
(text_editor/courtyard_design.py): от подъезда к подъезду, с выходом к
парковке; здесь -- для нескольких дворов участка, а не одного. Дорожка --
зона (полоса шириной PATH_WIDTH_M), а не плитки-объекты: так её учитывают
нормы, газон, проверка нарушений и экспорт в DXF (слой NEW_PATHS, при
повторной загрузке читается как пешеходная дорожка).

Существующие деревья и кусты дорожка обходит (_around_plants): край полосы
-- не ближе нормы посадки от дорожки, иначе дерево из чертежа оказывалось
прямо на новой плитке и подсвечивалось нарушением.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, substring, unary_union

from core.placement import Placer, polygons
from core.placement_geometry import lines_of, sample_line
from core.plant_catalog import CatalogItem
from core.schemas import Point2, Point3, RestrictionZone, Scene, SceneObject
from core.setback_norms import plant_kind_of_object_type, setback_for
from greenplan.options import GreenPlanOptions
from greenplan.zone_partitioning import _iter_polygons
from text_editor.courtyard_design import BENCH_OFFSET_M, LAMP_OFFSET_M, CourtyardDesigner
from text_editor.courtyard_layout import (
    ENCLOSURE_MIN_BUILDINGS,
    ENCLOSURE_MIN_RATIO,
    ENTRANCE_LINK_LEAK_M,
    ENTRANCE_REACH_M,
)

# Зоны и объекты, которые добавляет GreenPlan: по ним повторный запуск
# убирает прошлый результат (api/greenplan.py), а записка находит дорожки.
PATH_ZONE_PREFIX = "greenplan_path_"
PATH_LAYER = "NEW_PATHS"  # подстрока PATH -- парсер читает слой как пешеходную дорожку
SOURCE = "greenplan"

PATH_WIDTH_M = 1.5  # дворовая дорожка на два встречных пешехода
# Шаги вдоль дорожек -- реже, чем у дизайна одного двора (14 и 12 м): здесь
# дорожки во всех дворах участка, и скамейка каждые 12 м давала сотню с
# лишним скамеек. Парковые светильники высотой ~4 м ставят через 20-25 м,
# скамейки -- у мест отдыха, а не сплошным рядом.
LAMP_STEP_M = 20.0
BENCH_STEP_M = 30.0
MAX_YARDS = 3  # дворов с новыми дорожками на участок
# Фонари вдоль СУЩЕСТВУЮЩИХ дорожек: снаружи от края полосы (зазор МАФ от
# зоны 0,3 м + опора), не чаще LAMP_STEP_M, не больше MAX_LAMPS_ON_EXISTING.
EXISTING_PATH_LAMP_OFFSET_M = 0.8
MAX_LAMPS_ON_EXISTING = 300

ITEM_IDS = {"lamp": "lamp", "bench": "bench", "trash": "trash"}

# Обход существующего дерева или куста: ось дорожки -- не ближе половины
# ширины полосы плюс норма посадки от дорожки (СП 42.13330.2016, табл. 9.1:
# дерево 0,7 м, кустарник 0,5 м; колючим -- 2 м) с запасом на округление
# окружности многоугольником. Дуга обхода длиннее прямой не больше чем на
# MAX_DETOUR_EXTRA_M -- иначе (ряд деревьев поперёк) дорожка прерывается.
DETOUR_EPS_M = 0.05
MAX_DETOUR_EXTRA_M = 10.0


@dataclass
class Improvements:
    zones: list[RestrictionZone] = field(default_factory=list)
    objects: list[SceneObject] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def is_greenplan_zone(zone: RestrictionZone) -> bool:
    return zone.id.startswith(PATH_ZONE_PREFIX)


def plan_improvements(scene: Scene, catalog: dict[str, CatalogItem], options: GreenPlanOptions) -> Improvements:
    result = Improvements()
    if not (options.paths or options.lighting or options.benches):
        return result
    placer = Placer(scene)
    if placer.site is None:
        result.notes.append("благоустройство не добавлено: у участка нет границы")
        return result

    def create(item: CatalogItem, x: float, z: float, rotation_deg: float) -> None:
        obj_id = f"{item.object_type}_gp_{len(result.objects) + 1:03d}"
        result.objects.append(
            SceneObject(
                id=obj_id,
                type=item.object_type,
                model=item.model,
                position=Point3(x=x, y=0.0, z=z),
                rotation=math.radians(rotation_deg),
                scale=1.0,
                metadata={"catalogId": item.id, "label": item.label, "generated": True, "source": SOURCE},
            )
        )
        placer.occupy(obj_id, x, z, item.object_type)

    designer = CourtyardDesigner(scene, placer, create)
    axes = _new_path_axes(designer) if options.paths else []
    if options.paths and not axes:
        result.notes.append("дорожки не добавлены: на участке нет двора без зданий, проездов и площадок площадью от 150 м²")
    if axes:
        axes = _around_plants(axes, _plant_keepout(scene, PATH_WIDTH_M / 2 + DETOUR_EPS_M), designer.paving)
    designer.axes = axes
    designer._axes_union = unary_union(axes) if axes else None

    lamp, bench, trash = (catalog.get(ITEM_IDS[key]) for key in ("lamp", "bench", "trash"))
    if options.lighting and lamp is not None:
        _lamps_along_new_paths(designer, axes, lamp, result)
        _lamps_along_existing_paths(scene, placer, designer, lamp, result)
    if options.benches and bench is not None:
        if axes:
            designer.along([bench], BENCH_STEP_M, BENCH_OFFSET_M, both_sides=False, oriented=True, companion=trash)
        else:
            result.notes.append("скамейки и урны ставятся вдоль новых дорожек — включите «Дорожки»")

    result.zones = _path_zones(axes, placer, _plant_keepout(scene, DETOUR_EPS_M / 5))
    return result


def _plant_keepout(scene: Scene, reach: float):
    """Круги вокруг существующих деревьев и кустов, куда не заходит дорожка:
    радиус -- норма посадки от дорожки (по виду и породе) плюс reach. None --
    посадок нет."""
    discs = []
    for obj in scene.objects:
        kind = plant_kind_of_object_type(obj.type)
        if kind is None:
            continue
        setback = setback_for("pedestrian_path", kind, 0.0, species=obj.metadata.get("species"))
        discs.append(Point(obj.position.x, obj.position.z).buffer(setback + reach, quad_segs=8))
    return unary_union(discs) if discs else None


def _around_plants(axes: list, keepout, passable) -> list:
    """Оси дорожек в обход существующих деревьев и кустов: кусок оси внутри
    запретного круга (или пятна из слившихся кругов) заменяется более
    короткой дугой по его краю, если дуга не выходит из мостимой области
    (passable) и не намного длиннее; иначе дорожка здесь прерывается."""
    if keepout is None:
        return axes
    blobs = polygons(keepout)
    result: list = []
    for axis in axes:
        if not axis.intersects(keepout):
            result.append(axis)
            continue
        result += lines_of(axis.difference(keepout))
        for blob in blobs:
            ring = LineString(blob.exterior.coords)
            for inside in lines_of(axis.intersection(blob)):
                arc = _detour(ring, Point(inside.coords[0]), Point(inside.coords[-1]), inside.length, passable)
                if arc is not None:
                    result.append(arc)
    merged = linemerge(result) if result else None
    return [g for g in lines_of(merged) if g.length >= 1.0]


def _detour(ring: LineString, a: Point, b: Point, straight: float, passable):
    """Более короткая из двух дуг контура ring от a до b, что лежит в
    passable; None -- концы не на контуре (ось кончается внутри круга или
    входит в дыру пятна) или обе дуги не годятся."""
    if ring.distance(a) > 1e-6 or ring.distance(b) > 1e-6:
        return None
    da, db = ring.project(a), ring.project(b)
    lo, hi = sorted((da, db))
    if hi - lo < 1e-9:
        return None
    direct = list(substring(ring, lo, hi).coords)
    wrapped = list(substring(ring, hi, ring.length).coords) + list(substring(ring, 0.0, lo).coords)[1:]
    for coords in sorted((direct, wrapped), key=lambda c: LineString(c).length if len(c) >= 2 else math.inf):
        if len(coords) < 2:
            continue
        arc = LineString(coords)
        if arc.length - straight > MAX_DETOUR_EXTRA_M:
            continue
        if passable is not None and arc.difference(passable).length > ENTRANCE_LINK_LEAK_M:
            continue
        # Концы -- ровно точки оси: тогда linemerge склеит обход с дорожкой.
        start, end = (a, b) if (coords is direct) == (da <= db) else (b, a)
        return LineString([(start.x, start.y), *coords[1:-1], (end.x, end.y)])
    return None


def _new_path_axes(designer: CourtyardDesigner) -> list:
    """Оси новых дорожек в дворах участка: самый "дворовый" кусок свободной
    площади (CourtyardDesigner.area), затем следующие -- только если это
    тоже двор (окружён зданиями) или к нему выходят подъезды: дорожки в
    полосе вдоль улицы без входов не нужны."""
    axes: list = []
    for index in range(MAX_YARDS):
        poly = designer.area(None, None, 0.0)
        if poly is None:
            break
        if index > 0 and not _is_yard(designer, poly):
            break
        _, _, network, _, _ = designer.layout(poly, "auto", want_fountain=False)
        axes += [g for line in network for g in lines_of(line.intersection(designer.paving)) if g.length >= 1.0]
        designer.free = designer.free.difference(poly.buffer(1.0))
    return axes


def _is_yard(designer: CourtyardDesigner, poly) -> bool:
    ratio, near_buildings = designer._enclosure(poly)
    if ratio >= ENCLOSURE_MIN_RATIO and near_buildings >= ENCLOSURE_MIN_BUILDINGS:
        return True
    return any(poly.distance(e) <= ENTRANCE_REACH_M for e in designer.entrances)


def _path_zones(axes: list, placer: Placer, keepout=None) -> list[RestrictionZone]:
    if not axes:
        return []
    strip = unary_union([axis.buffer(PATH_WIDTH_M / 2) for axis in axes]).intersection(placer.site)
    if keepout is not None:
        # Страховка к _around_plants: полоса не подходит к стволу ближе нормы,
        # даже где обход не сложился.
        strip = strip.difference(keepout)
    zones = []
    # Кольцевая дорожка -- полигон с дыркой; зона хранит один контур, поэтому
    # такой полигон режется на куски без дыр (иначе двор внутри кольца стал
    # бы "дорожкой").
    for poly in _iter_polygons(strip):
        if poly.area < 1.0:
            continue
        zones.append(
            RestrictionZone(
                id=f"{PATH_ZONE_PREFIX}{len(zones) + 1:03d}",
                type="pedestrian_path",
                name=PATH_LAYER,
                polygon=[Point2(x=x, z=z) for x, z in list(poly.exterior.coords)[:-1]],
                severity="warning",
                minDistance=0.5,
                message="Новая дорожка (GreenPlan)",
            )
        )
    return zones


# Если место фонаря занято (охранная зона сети, соседний объект), пробуем
# другую сторону дорожки и сдвиг вдоль неё -- а не оставляем пролёт тёмным.
LAMP_SHIFTS_M = (0.0, 4.0, -4.0, 8.0, -8.0)


def _lamps_along_new_paths(designer: CourtyardDesigner, axes: list, lamp: CatalogItem, result: Improvements) -> None:
    """Фонари зигзагом вдоль новых дорожек через LAMP_STEP_M. Опору в
    охранную зону сети не ставим (Placer: зоны с отступом), а дорожка через
    неё пройти может -- поэтому на участках, густо пересечённых сетями,
    фонарь ищет место рядом: другая сторона, сдвиг до 8 м вдоль дорожки."""
    spots = placed = 0
    for piece in axes:
        for x, z, tx, tz in sample_line(piece, LAMP_STEP_M):
            first = 1.0 if spots % 2 == 0 else -1.0
            spots += 1
            done = False
            for shift in LAMP_SHIFTS_M:
                sx, sz = x + tx * shift, z + tz * shift
                for side in (first, -first):
                    px, pz = sx - tz * LAMP_OFFSET_M * side, sz + tx * LAMP_OFFSET_M * side
                    if designer._put(lamp, px, pz, rotation=0.0):
                        done = True
                        break
                if done:
                    placed += 1
                    break
    if spots and placed < spots / 2:
        result.notes.append(
            f"вдоль новых дорожек поставлено {placed} фонарей из {spots} по шагу {LAMP_STEP_M:.0f} м: остальные места "
            "попадают в охранные зоны инженерных сетей — опоры там без согласования с владельцем сети не ставят"
        )


def _lamps_along_existing_paths(
    scene: Scene, placer: Placer, designer: CourtyardDesigner, lamp: CatalogItem, result: Improvements
) -> None:
    """Фонари вдоль уже существующих дорожек -- снаружи от края полосы, не
    чаще LAMP_STEP_M с учётом уже стоящих фонарей (свет на участке мог быть
    и до GreenPlan)."""
    lamps = [(o.position.x, o.position.z) for o in scene.objects if o.type == "lamp"]
    lamps += [(o.position.x, o.position.z) for o in result.objects if o.type == "lamp"]
    min_gap = 0.75 * LAMP_STEP_M
    added = 0
    for zone, geom in placer.zones:
        if zone.type != "pedestrian_path":
            continue
        for poly in polygons(geom):
            # Против часовой стрелки: снаружи -- справа от направления обхода.
            ring = orient(poly, 1.0).exterior
            for x, z, tx, tz in sample_line(ring, LAMP_STEP_M):
                px, pz = x + tz * EXISTING_PATH_LAMP_OFFSET_M, z - tx * EXISTING_PATH_LAMP_OFFSET_M
                if any(math.hypot(px - lx, pz - lz) < min_gap for lx, lz in lamps):
                    continue
                if added >= MAX_LAMPS_ON_EXISTING:
                    result.notes.append(
                        f"вдоль существующих дорожек поставлено {MAX_LAMPS_ON_EXISTING} фонарей — "
                        "дальше участок слишком велик для автоматической расстановки"
                    )
                    return
                if designer._put(lamp, px, pz, rotation=0.0):
                    lamps.append((px, pz))
                    added += 1
