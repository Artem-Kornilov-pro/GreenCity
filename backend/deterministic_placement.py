"""
Детерминированная расстановка новой посадки -- GreenPlan, Этап 5 (issue #23).
Полностью без LLM: точки считает геометрия, а не языковая модель (тот же
принцип, что и в placement.py -- см. его докстринг). Каждая зона
(zone_partitioning.GeometricZone) уже получила паттерн от pattern_assignment
(Этап 4); эта функция превращает пару (зона, паттерн) в список SceneObject,
проверяя нормативный отступ у КАЖДОЙ точки через placer.is_free ВО ВРЕМЯ
расстановки -- не постфактум-фильтром, как одноразовые скрипты прошлой
сессии (см. память проекта про `post_filter.py`).

`Placer` строится ОДИН РАЗ из того же `Scene`, что ушёл в `partition_zones` --
вся геометрия (характеризация участка, разбиение на зоны, расстановка) сверяется
с одним и тем же источником правды, поэтому класс бага "моя реконструкция
геометрии разошлась с реальным парсером" (прошлая сессия наступала на него в
каждом одноразовом генераторе) здесь структурно исключён.

Три geometry_family из pattern_library.py -- три алгоритма, каждый по одному
единому семейству паттернов (issue #23, Этап 5: "линейные паттерны -- один
параметризуемый алгоритм", "площадные заливки -- Пуассоновское распределение
или сетка"):

* linear    -- ряд(ы) точек вдоль centerline_of(зона) (placement.py), с
               опциональной второй линией (PatternSpec.double_row_offset_m).
* area_fill -- кандидаты по всей площади зоны (Placer.points_in_area) + ровный
               отбор (pick_spread).
* clustered -- несколько центров по зоне (points_in_area + pick_spread), у
               каждого -- компактная группа соседей (pick_near), один вид
               растения на группу.

Виды растений (species/CatalogItem) выбирает вызывающий код через параметры
trees/bushes -- этот модуль знает только геометрию, не ассортимент (тот же
принцип разделения, что в pattern_library.py).
"""

from __future__ import annotations

from pattern_assignment import ZoneAssignment, assign_patterns
from pattern_library import PATTERN_LIBRARY, PatternSpec
from placement import Placer, centerline_of, lines_of, pick_near, pick_spread, rotation_of, sample_line
from plant_catalog import CatalogItem
from schemas import Point3, Scene, SceneObject
from shapely.geometry import Polygon
from zone_partitioning import GeometricZone, partition_zones

# Шаг ряда кустов вдоль линейного паттерна и во сколько раз реже вдоль той же
# линии идут деревья -- по реальному соотношению из 02_peschany_pereulok
# (кусты 1.4 м / деревья 12 м, ~8.5x) и 19_2ya_pryadilnaya (изгородь у дороги).
LINEAR_BUSH_STEP_M = 1.4
LINEAR_TREE_STEP_FACTOR = 8.5

# Площадная заливка: шаг сетки кандидатов и во сколько раз реже деревья --
# по 20_makeeva_s (кусты 6 м / деревья 12 м, 2x).
AREA_FILL_BUSH_SPACING_M = 6.0
AREA_FILL_TREE_SPACING_M_FACTOR = 2.0

# Рощи: площадь на одну группу и разнос между центрами/членами группы --
# по 12_natashinsky_proezd (25 роще на явно небольшой открытой площади).
CLUSTER_AREA_PER_GROUP_SQM = 200.0
CLUSTER_CENTER_SPACING_M = 8.0
CLUSTER_MEMBER_SPACING_M = 2.0


def _polygon_of(zone: GeometricZone) -> Polygon:
    return Polygon([(p.x, p.z) for p in zone.polygon])


def _scene_object(
    key: str, item: CatalogItem, x: float, z: float, rotation: float, zone: GeometricZone, assignment: ZoneAssignment
) -> SceneObject:
    return SceneObject(
        id=key,
        type=item.object_type,
        model=item.model,
        position=Point3(x=x, y=0.0, z=z),
        rotation=rotation,
        scale=1.0,
        metadata={
            "species": item.label,
            "category": "vegetation",
            "generated": True,
            "catalogId": item.id,
            "pattern_id": assignment.pattern_id,
            "zone_id": zone.id,
            "source_project": assignment.source_project,
        },
    )


def _place_row(
    placer: Placer,
    zone: GeometricZone,
    assignment: ZoneAssignment,
    items: list[CatalogItem],
    kind: str,
    step: float,
    row_offset: float,
    line,
    counter: list[int],
) -> list[SceneObject]:
    if not items:
        return []
    objects = []
    for x, z, tx, tz in sample_line(line, step):
        px, pz = x - tz * row_offset, z + tx * row_offset
        item = items[counter[0] % len(items)]
        if not placer.is_free(px, pz, kind, obj_type=item.object_type):
            continue
        counter[0] += 1
        key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
        placer.occupy(key, px, pz, item.object_type)
        objects.append(_scene_object(key, item, px, pz, rotation_of(tx, tz), zone, assignment))
    return objects


def _place_linear(
    placer: Placer,
    zone: GeometricZone,
    spec: PatternSpec,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    centerline = centerline_of(_polygon_of(zone))
    if centerline is None:
        return []
    offsets = (0.0,) if spec.double_row_offset_m <= 0 else (0.0, spec.double_row_offset_m)
    objects: list[SceneObject] = []
    bush_counter, tree_counter = [0], [0]
    for line in lines_of(centerline):
        for row_offset in offsets:
            objects += _place_row(placer, zone, assignment, bushes, "bush", LINEAR_BUSH_STEP_M, row_offset, line, bush_counter)
        objects += _place_row(
            placer, zone, assignment, trees, "tree", LINEAR_BUSH_STEP_M * LINEAR_TREE_STEP_FACTOR, 0.0, line, tree_counter
        )
    return objects


def _place_area_fill(
    placer: Placer,
    zone: GeometricZone,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    poly = _polygon_of(zone)
    if poly.is_empty or poly.area <= 0:
        return []
    objects: list[SceneObject] = []
    for items, kind, spacing in (
        (bushes, "bush", AREA_FILL_BUSH_SPACING_M),
        (trees, "tree", AREA_FILL_BUSH_SPACING_M * AREA_FILL_TREE_SPACING_M_FACTOR),
    ):
        if not items:
            continue
        target = max(0, round(poly.area / spacing**2))
        if target == 0:
            continue
        candidates = placer.points_in_area(kind, spacing / 2, within=poly, max_candidates=target * 20)
        chosen = pick_spread(candidates, target, spacing * 0.8)
        counter = [0]
        for x, z in chosen:
            item = items[counter[0] % len(items)]
            if not placer.is_free(x, z, kind, obj_type=item.object_type):
                continue
            counter[0] += 1
            key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
            placer.occupy(key, x, z, item.object_type)
            objects.append(_scene_object(key, item, x, z, 0.0, zone, assignment))
    return objects


def _place_clustered(
    placer: Placer,
    zone: GeometricZone,
    spec: PatternSpec,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    items = trees or bushes
    if not items:
        return []
    kind = items[0].setback_kind
    poly = _polygon_of(zone)
    if poly.is_empty or poly.area <= 0:
        return []

    group_count = max(1, round(poly.area / CLUSTER_AREA_PER_GROUP_SQM))
    pool = placer.points_in_area(kind, CLUSTER_MEMBER_SPACING_M / 2, within=poly, max_candidates=group_count * 60)
    if not pool:
        return []
    centers = pick_spread(pool, group_count, CLUSTER_CENTER_SPACING_M)

    objects: list[SceneObject] = []
    counter = [0]
    for group_index, center in enumerate(centers):
        item = items[group_index % len(items)]  # один вид на всю группу -- как в 19_2ya_pryadilnaya
        target_size = spec.group_size[1]
        members = pick_near(pool, target_size, CLUSTER_MEMBER_SPACING_M, center)
        for x, z in members:
            if not placer.is_free(x, z, kind, obj_type=item.object_type):
                continue
            counter[0] += 1
            key = f"{item.object_type}_{zone.id}_{counter[0]:03d}"
            placer.occupy(key, x, z, item.object_type)
            objects.append(_scene_object(key, item, x, z, (counter[0] * 137.5) % 360, zone, assignment))
    return objects


def place_zone(
    placer: Placer,
    zone: GeometricZone,
    assignment: ZoneAssignment,
    trees: list[CatalogItem],
    bushes: list[CatalogItem],
) -> list[SceneObject]:
    spec = PATTERN_LIBRARY[assignment.pattern_id]
    if spec.geometry_family == "linear":
        return _place_linear(placer, zone, spec, assignment, trees, bushes)
    if spec.geometry_family == "clustered":
        return _place_clustered(placer, zone, spec, assignment, trees, bushes)
    return _place_area_fill(placer, zone, assignment, trees, bushes)


def generate_for_scene(
    scene: Scene,
    trees: list[CatalogItem] | None = None,
    bushes: list[CatalogItem] | None = None,
    k: int = 3,
) -> tuple[list[SceneObject], list[ZoneAssignment]]:
    """Полный проход Этапов 3-5 на одной сцене: зонирование -> retrieval и
    назначение паттернов -> детерминированная расстановка. Возвращает только
    НОВЫЕ объекты (как generate_trees/generate_bushes в greenery_generator.py
    -- вызывающий код сам решает, добавлять ли их в scene.objects) и список
    ZoneAssignment с provenance -- на нём в следующей итерации строится
    Этап 6 (отчёт "по аналогии с проектом X"), без переделки этой функции."""
    trees = trees or []
    bushes = bushes or []
    zones = partition_zones(scene)
    assignments = assign_patterns(scene, zones, k=k)
    placer = Placer(scene)

    objects: list[SceneObject] = []
    for zone, assignment in zip(zones, assignments):
        objects += place_zone(placer, zone, assignment, trees, bushes)
    return objects, assignments
