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
from placement import (
    Placer,
    centerline_of,
    lines_of,
    pick_near,
    pick_spread,
    rect_sides,
    rotation_of,
    rows_of,
    sample_line,
    wavy_line,
)
from plant_catalog import CatalogItem
from schemas import Point3, Scene, SceneObject
from shapely.geometry import Polygon
from zone_partitioning import GeometricZone, partition_zones

# Шаг ряда кустов вдоль линейного паттерна и во сколько раз реже вдоль той же
# линии идут деревья -- по реальному соотношению из 02_peschany_pereulok
# (кусты 1.4 м / деревья 12 м, ~8.5x) и 19_2ya_pryadilnaya (изгородь у дороги).
LINEAR_BUSH_STEP_M = 1.4
LINEAR_TREE_STEP_FACTOR = 8.5

# Расстояние между параллельными рядами для linear-паттерна на open_area
# (rows_of вместо одной центральной линии, см. её докстринг) -- то же
# расстояние, что и у шага заливки массива (AREA_FILL_BUSH_SPACING_M ниже),
# для сопоставимой плотности между линейными и площадными паттернами на
# одном и том же виде зоны.
OPEN_AREA_ROW_SPACING_M = 6.0
# Верхняя граница числа рядов на одну open_area-зону -- без неё гигантская
# зона (тот же реальный случай 646 279 м², что и у MAX_OBJECTS_PER_ZONE ниже)
# даёт 36 рядов по многие сотни кустов КАЖДЫЙ (один длинный ряд сам по себе
# уже плотный при шаге LINEAR_BUSH_STEP_M=1.4 м) -- полный пайплайн на этом
# файле подскочил с ~11 до ~20 секунд и почти 9000 новых объектов на одну
# сцену при добавлении rows_of (было: одна линия, тот же баг класса "гигант-
# ская зона", что и у MAX_OBJECTS_PER_ZONE/MAX_GROUPS_PER_ZONE, только для
# другого geometry_family). 10 рядов даёт сопоставимый с area_fill/clustered
# порядок итоговой плотности на такой зоне вместо кратного превышения.
MAX_ROWS_PER_ZONE = 10

# Площадная заливка: шаг сетки кандидатов и во сколько раз реже деревья --
# по 20_makeeva_s (кусты 6 м / деревья 12 м, 2x).
AREA_FILL_BUSH_SPACING_M = 6.0
AREA_FILL_TREE_SPACING_M_FACTOR = 2.0

# Рощи: площадь на одну группу и разнос между центрами/членами группы --
# по 12_natashinsky_proezd (25 роще на явно небольшой открытой площади).
CLUSTER_AREA_PER_GROUP_SQM = 200.0
CLUSTER_CENTER_SPACING_M = 8.0
CLUSTER_MEMBER_SPACING_M = 2.0

# Верхняя граница числа объектов/групп на ОДНУ зону -- без неё площадная
# заливка или рощи на очень крупной зоне (найдено на реальном случае:
# locations/location_old/05_klykova_avenue, район 2 км², одна open_area зона
# 646 279 м²) требуют target ~18 000, а pick_spread -- k-средних с k=target,
# чья стоимость на итерацию растёт как O(точки x k): при k в десятки тысяч
# один вызов уходит в минуты вместо долей секунды и на практике выглядит как
# зависшее приложение (issue "приложение работает нестабильно, зависает").
# Тот же класс проблемы уже решён для того же файла в courtyard_design.py
# (MAX_DESIGN_AREA_M2) и для генератора по сетке в greenery_generator.py
# (MAX_GENERATED_TREES=400/MAX_GENERATED_BUSH_CLUSTERS=60, те же числа ниже
# для согласованности) -- здесь тот же приём, только для одной зоны, а не
# для всего дизайна: сверх этой границы зона получает столько объектов,
# сколько уместилось при разумной плотности, а не по одному на каждый
# квадратный метр гигантской территории.
MAX_OBJECTS_PER_ZONE = 400
MAX_GROUPS_PER_ZONE = 60


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
    poly = _polygon_of(zone)

    if spec.line_shape == "ring":
        # building_ring: зона -- узкая полоса-бублик вокруг здания
        # (building_border из zone_partitioning.py). Линия через центроид
        # (centerline_of/rows_of) срезала бы такую зону ХОРДОЙ по прямой,
        # а не обходила здание по кругу -- визуально не "кольцо", а
        # случайный отрезок внутри кольца. Собственный контур полигона --
        # и есть то самое кольцо.
        row_lines = [poly.exterior]
    elif zone.kind == "open_area":
        # open_area -- произвольной ширины двумерное пятно
        # (zone_partitioning.py: "всё, что осталось: под площадные
        # паттерны"), а не узкая полоса постоянной ширины вроде
        # building_border/path_corridor/site_edge. Одна линия через
        # середину покрывала только ряд посередине зоны, оставляя
        # остальную ширину открытой площади пустой -- реальная находка
        # ручного тестирования ("не заполняет всё пространство") на
        # linear-паттернах (flowing_rows/diagonal_rows), которые retrieval
        # назначает open_area наравне с площадными. rows_of() даёт столько
        # параллельных рядов, сколько уместится по ширине зоны.
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        row_lines = rows_of(poly, OPEN_AREA_ROW_SPACING_M, angle_offset_deg=angle)
        if len(row_lines) > MAX_ROWS_PER_ZONE:
            # Равномерная прорезка, а не первые MAX_ROWS_PER_ZONE -- rows_of
            # выдаёт ряды по порядку поперёк всей ширины зоны, взять только
            # первые N означало бы покрыть рядами одну сторону гигантской
            # зоны, оставив другую совсем пустой.
            step = len(row_lines) / MAX_ROWS_PER_ZONE
            row_lines = [row_lines[round(i * step)] for i in range(MAX_ROWS_PER_ZONE)]
    else:
        angle = spec.diagonal_angle_deg if spec.line_shape == "diagonal" else 0.0
        centerline = centerline_of(poly, angle_offset_deg=angle)
        row_lines = [centerline] if centerline is not None else []

    if spec.line_shape == "wavy" and spec.wave_amplitude_m > 0:
        # Амплитуда из корпуса (4 м у 10_stary_gay) может быть шире самой
        # зоны (path_corridor -- полоса ~3 м) -- тогда волна почти всё время
        # уходила бы за пределы зоны и после обрезки по контуру рассыпалась
        # бы на мелкие несвязные обрывки у каждого пересечения нуля синуса.
        # Урезаем амплитуду под собственную ширину (короткую сторону)
        # каждой конкретной зоны -- на открытой достаточно широкой зоне
        # (open_area) применяется полная документированная амплитуда.
        short_side = min(s[2] for s in rect_sides(poly))
        amplitude = min(spec.wave_amplitude_m, short_side * 0.35)
        wavy = [wavy_line(line, amplitude, spec.wave_length_m) for line in row_lines]
        row_lines = [w.intersection(poly) for w in wavy if w is not None]

    if not row_lines:
        return []
    offsets = (0.0,) if spec.double_row_offset_m <= 0 else (0.0, spec.double_row_offset_m)
    objects: list[SceneObject] = []
    bush_counter, tree_counter = [0], [0]
    for row_centerline in row_lines:
        for line in lines_of(row_centerline):
            # Деревья -- ПЕРЕД кустами на той же линии (offset=0.0 у обоих).
            # Куст с шагом LINEAR_BUSH_STEP_M=1.4 м занимает буквально каждую
            # точку центральной линии -- ближайший куст к любой другой точке
            # той же линии всегда ближе OBJECT_CLEARANCE_M=1.0 м. Если сажать
            # кусты первыми (как было раньше), ни одна точка-кандидат для
            # дерева на этой же линии не проходит is_free -- деревья не
            # появлялись ВООБЩЕ ни на одном линейном паттерне (issue:
            # "деревья вообще не появляются", реальный баг с
            # 01_single_building/02_courtyard). Деревья реже (шаг в
            # LINEAR_TREE_STEP_FACTOR=8.5 раз больше) и сами по себе не
            # мешают кустам занять остальную линию после них.
            objects += _place_row(
                placer, zone, assignment, trees, "tree", LINEAR_BUSH_STEP_M * LINEAR_TREE_STEP_FACTOR, 0.0, line, tree_counter
            )
            for row_offset in offsets:
                objects += _place_row(placer, zone, assignment, bushes, "bush", LINEAR_BUSH_STEP_M, row_offset, line, bush_counter)
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
        target = min(max(0, round(poly.area / spacing**2)), MAX_OBJECTS_PER_ZONE)
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

    group_count = min(max(1, round(poly.area / CLUSTER_AREA_PER_GROUP_SQM)), MAX_GROUPS_PER_ZONE)
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
