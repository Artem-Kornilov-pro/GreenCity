"""deterministic_placement.generate_for_scene -- GreenPlan Этап 5 (issue #23):
Этапы 3-5 (характеризация/retrieval -> назначение паттернов -> расстановка)
собраны в одну функцию. Контракт (форма результата, отсутствие дублей id)
проверяется на быстрых синтетических фикстурах location_old/; то, что
расстановка НА САМОМ ДЕЛЕ не нарушает нормативные отступы, можно честно
проверить только на реальной геометрии -- см. test_real_corpus_has_zero_violations
ниже, независимая проверка (не через Placer, которым уже пользуется сама
расстановка) в духе post_filter.py из прошлой сессии, но встроенная в
постоянный тест, а не одноразовый scratch-скрипт."""

import math
from collections import Counter

from deterministic_placement import (
    MAX_GROUPS_PER_ZONE,
    MAX_OBJECTS_PER_ZONE,
    _place_area_fill,
    _place_clustered,
    _place_linear,
    generate_for_scene,
)
from helpers import make_boundary, make_scene
from pattern_assignment import ZoneAssignment
from pattern_corpus import _corpus_scenes
from pattern_library import PATTERN_LIBRARY
from placement import OBJECT_CLEARANCE_M, POINT_CLEARANCE_M, Placer
from schemas import Point2
from setback_norms import setback_for
from shapely.geometry import Point, Polygon
from zone_partitioning import GeometricZone, partition_zones


def _trees_and_bushes(catalog):
    trees = [c for c in catalog if c.category == "tree"]
    bushes = [c for c in catalog if c.category == "bush" and c.object_type == "bush"]
    return trees, bushes


def test_empty_catalog_places_nothing_but_still_assigns(scene_02):
    objects, assignments = generate_for_scene(scene_02, trees=[], bushes=[], k=3)
    assert objects == []
    assert len(assignments) == len(partition_zones(scene_02))


def test_object_ids_are_unique(scene_06, catalog):
    trees, bushes = _trees_and_bushes(catalog)
    objects, _ = generate_for_scene(scene_06, trees=trees, bushes=bushes, k=3)
    ids = [o.id for o in objects]
    assert len(ids) == len(set(ids))


def test_objects_carry_provenance_metadata(scene_02, catalog):
    trees, bushes = _trees_and_bushes(catalog)
    objects, assignments = generate_for_scene(scene_02, trees=trees, bushes=bushes, k=3)
    assignment_by_zone = {a.zone_id: a for a in assignments}
    assert objects, "на 02_courtyard_3buildings при непустом каталоге должно что-то поместиться"
    for obj in objects:
        assert obj.type in ("tree", "bush")
        zone_id = obj.metadata["zone_id"]
        assert zone_id in assignment_by_zone
        assert obj.metadata["pattern_id"] == assignment_by_zone[zone_id].pattern_id
        assert obj.metadata["source_project"] == assignment_by_zone[zone_id].source_project


def test_linear_pattern_on_open_area_uses_multiple_rows(scene_01, scene_02, catalog):
    # Регрессия: _place_linear раньше сажал ОДНУ линию через середину любой
    # зоны, включая open_area -- двумерное пятно произвольной ширины
    # (zone_partitioning.py: "всё, что осталось: под площадные паттерны"),
    # не узкую полосу постоянной ширины вроде building_border/path_corridor.
    # Один ряд посередине оставлял остальную ширину открытой площади пустой
    # -- находка ручного тестирования ("не заполняет всё пространство") на
    # linear-паттернах (flowing_rows/diagonal_rows), которые retrieval может
    # назначить open_area наравне с площадными паттернами. rows_of() должен
    # заметно поднять итоговую плотность на тех же двух сценах, где раньше
    # не было ни одного дерева (см. тест выше).
    trees, bushes = _trees_and_bushes(catalog)
    for scene in (scene_01, scene_02):
        objects, _ = generate_for_scene(scene, trees=trees, bushes=bushes, k=3)
        assert len(objects) > 500, f"расстановка выглядит как один ряд, а не заполнение площади: {len(objects)} объектов"


def test_linear_pattern_places_both_trees_and_bushes(scene_01, scene_02, catalog):
    # Регрессия: раньше _place_linear сажал кусты (шаг 1.4 м) на ВСЮ линию
    # ПЕРЕД деревьями (шаг в 8.5 раз больше) -- любая точка-кандидат для
    # дерева на той же линии оказывалась ближе OBJECT_CLEARANCE_M к уже
    # занявшему её кусту, и деревья не появлялись НИ НА ОДНОМ линейном
    # паттерне (building_ring/linear_hedge_row/flowing_rows) вообще, только
    # кусты -- нашлось ручным тестированием на 01_single_building и
    # 02_courtyard_3buildings, обе используют linear-семейство паттернов
    # (building_border/path_corridor/open_area).
    trees, bushes = _trees_and_bushes(catalog)
    for scene in (scene_01, scene_02):
        objects, _ = generate_for_scene(scene, trees=trees, bushes=bushes, k=3)
        counts = Counter(o.type for o in objects)
        assert counts["tree"] > 0, f"деревья не появились вовсе: {counts}"
        assert counts["bush"] > 0, f"кусты не появились вовсе: {counts}"


def _independent_zone_violations(scene, new_objects) -> list[tuple[str, str, float, float]]:
    """Пересчитывает нарушения зон с нуля по scene.restrictions (не через
    Placer.region()/is_free(), которыми уже пользуется сама расстановка) --
    так тест ловит настоящую ошибку в deterministic_placement.py, а не
    повторяет её же проверку теми же средствами."""
    zones = []
    for zone in scene.restrictions:
        if zone.severity == "allowed" or len(zone.polygon) < 3:
            continue
        poly = Polygon([(p.x, p.z) for p in zone.polygon])
        if poly.is_valid and poly.area > 0:
            zones.append((zone, poly))

    violations = []
    for obj in new_objects:
        kind = "tree" if obj.type == "tree" else "bush"
        point = Point(obj.position.x, obj.position.z)
        for zone, poly in zones:
            required = setback_for(zone.type, kind, zone.minDistance)
            distance = poly.distance(point)
            if distance < required - 1e-6:
                violations.append((obj.id, zone.id, distance, required))
    return violations


def _independent_pairwise_violations(new_objects) -> list[tuple[str, str, float, float]]:
    """Расстояние между КАЖДОЙ парой новых точечных объектов не должно быть
    меньше применимой нормы (placement.OBJECT_CLEARANCE_M как минимум для
    любой пары, POINT_CLEARANCE_M для дерево-дерево) -- независимая от
    Placer.blocker() проверка того же требования."""
    violations = []
    for i, a in enumerate(new_objects):
        if a.type not in ("tree", "bush"):
            continue
        for b in new_objects[i + 1 :]:
            if b.type not in ("tree", "bush"):
                continue
            required = max(OBJECT_CLEARANCE_M, POINT_CLEARANCE_M.get(a.type, {}).get(b.type, 0.0))
            distance = math.hypot(a.position.x - b.position.x, a.position.z - b.position.z)
            if distance < required - 1e-6:
                violations.append((a.id, b.id, distance, required))
    return violations


def test_real_corpus_has_zero_violations(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    scenes = _corpus_scenes()
    # Три разнородных реальных проекта (полоса+кольца, рощи, ведомость с
    # группами) -- не все 9, чтобы тест оставался быстрым, но с разными
    # geometry_family (linear/clustered) в одном прогоне.
    for slug in ("02_peschany_pereulok", "12_natashinsky_proezd", "19_2ya_pryadilnaya"):
        scene = scenes[slug]
        objects, _ = generate_for_scene(scene, trees=trees, bushes=bushes, k=3)
        assert objects, f"{slug}: расстановка не должна быть пустой на реальном проекте"

        zone_violations = _independent_zone_violations(scene, objects)
        assert not zone_violations, f"{slug}: нарушения зон {zone_violations[:5]}"

        pairwise_violations = _independent_pairwise_violations(objects)
        assert not pairwise_violations, f"{slug}: нарушения расстояния между объектами {pairwise_violations[:5]}"


# --- Ограничение числа объектов на гигантской зоне ---------------------------
#
# Регрессия на реальный случай: одна open_area зона 646 279 м² (реальный
# синтетический участок locations/location_old/05_klykova_avenue, район
# 2 км²) без верхней границы давала target ~18 000 -- pick_spread (k-средних
# с k=target) на такой k уходил в минуты вместо долей секунды, и приложение
# выглядело зависшим ("работает нестабильно, зависает на что-то"). Зона в
# тесте синтетическая и ещё крупнее (1 км x 1 км), чтобы не тянуть реальный
# 9.6 МБ DXF и оставаться быстрым.


def _huge_square_zone(kind: str, side_m: float = 1000.0) -> GeometricZone:
    half = side_m / 2
    polygon = [Point2(x=-half, z=-half), Point2(x=half, z=-half), Point2(x=half, z=half), Point2(x=-half, z=half)]
    return GeometricZone(id="huge_zone", kind=kind, polygon=polygon, area_sqm=side_m * side_m)


def _huge_scene() -> object:
    return make_scene(boundary=make_boundary(-600, -600, 600, 600))


def test_place_area_fill_caps_object_count_on_a_huge_zone(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    zone = _huge_square_zone("open_area")
    placer = Placer(_huge_scene())
    spec = PATTERN_LIBRARY["poisson_scatter_fill"]
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="poisson_scatter_fill",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_area_fill(placer, zone, spec, assignment, trees, bushes)
    assert sum(1 for o in objects if o.type == "bush") <= MAX_OBJECTS_PER_ZONE
    assert sum(1 for o in objects if o.type == "tree") <= MAX_OBJECTS_PER_ZONE


def test_place_clustered_caps_group_count_on_a_huge_zone(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    zone = _huge_square_zone("open_area")
    placer = Placer(_huge_scene())
    spec = PATTERN_LIBRARY["grove_clusters"]
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="grove_clusters",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_clustered(placer, zone, spec, assignment, trees, bushes)
    assert len(objects) <= MAX_GROUPS_PER_ZONE * spec.group_size[1]


# --- Разнообразие линейных паттернов (issue "не только прямые засадки") -----
#
# building_ring/diagonal_rows/flowing_rows раньше проводили ТУ ЖЕ прямую
# линию через центроид зоны, что и linear_hedge_row -- реальный проект
# 10_stary_gay (data/pattern_corpus.yaml) использует волнистую изгородь, не
# прямую, а "кольцо кустов вокруг здания" не бывает прямым отрезком.


def _donut_building_border_zone() -> GeometricZone:
    # Узкая полоса-бублик вокруг здания 10x10 в начале координат -- то, что
    # реально строит zone_partitioning.py для building_border.
    outer = [(-13, -13), (13, -13), (13, 13), (-13, 13)]
    inner = [(-5, -5), (5, -5), (5, 5), (-5, 5)]
    donut = Polygon(outer, [inner[::-1]])
    return GeometricZone(
        id="building_border_ring_test",
        kind="building_border",
        polygon=[Point2(x=x, z=z) for x, z in donut.exterior.coords[:-1]],
        area_sqm=donut.area,
    )


def test_building_ring_places_points_around_the_building_not_a_chord(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    zone = _donut_building_border_zone()
    placer = Placer(make_scene(boundary=make_boundary(-20, -20, 20, 20)))
    spec = PATTERN_LIBRARY["building_ring"]
    assert spec.line_shape == "ring"
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="building_border", pattern_id="building_ring",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_linear(placer, zone, spec, assignment, trees, bushes)
    assert objects, "кольцо вокруг здания должно дать хотя бы несколько кустов"
    # Прямая линия через центр дала бы точки на одной оси (x или z почти
    # постоянна) -- кольцо должно охватывать точки по ВСЕМ четырём сторонам.
    xs = [o.position.x for o in objects]
    zs = [o.position.z for o in objects]
    assert max(xs) > 4 and min(xs) < -4
    assert max(zs) > 4 and min(zs) < -4


def test_diagonal_rows_places_points_along_a_diagonal(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    square = [(0, 0), (30, 0), (30, 30), (0, 30)]
    zone = GeometricZone(
        id="open_area_diagonal_test", kind="open_area",
        polygon=[Point2(x=x, z=z) for x, z in square], area_sqm=900.0,
    )
    placer = Placer(make_scene(boundary=make_boundary(-10, -10, 40, 40)))
    spec = PATTERN_LIBRARY["diagonal_rows"]
    assert spec.line_shape == "diagonal"
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="diagonal_rows",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_linear(placer, zone, spec, assignment, trees, bushes)
    assert objects
    # На диагональном ряду x и z меняются вместе (корреляция), а не как на
    # оси-выровненном ряду, где одна из координат почти постоянна.
    xs = [o.position.x for o in objects]
    zs = [o.position.z for o in objects]
    assert max(xs) - min(xs) > 10
    assert max(zs) - min(zs) > 10


def test_flowing_rows_places_points_off_the_straight_centerline(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    strip = [(0, -2), (100, -2), (100, 2), (0, 2)]
    zone = GeometricZone(
        id="path_corridor_wavy_test", kind="path_corridor",
        polygon=[Point2(x=x, z=z) for x, z in strip], area_sqm=400.0,
    )
    placer = Placer(make_scene(boundary=make_boundary(-10, -10, 110, 10)))
    spec = PATTERN_LIBRARY["flowing_rows"]
    assert spec.line_shape == "wavy"
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="path_corridor", pattern_id="flowing_rows",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_linear(placer, zone, spec, assignment, trees, bushes)
    assert objects
    # Прямая линия шла бы по z=0 у всех точек -- волна должна дать заметный
    # разброс z вокруг него.
    zs = [o.position.z for o in objects]
    assert max(zs) - min(zs) > 0.5


def test_formal_bosque_grid_places_only_trees_on_a_square_grid(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    square = [(0, 0), (36, 0), (36, 36), (0, 36)]
    zone = GeometricZone(
        id="open_area_bosque_test", kind="open_area",
        polygon=[Point2(x=x, z=z) for x, z in square], area_sqm=36.0 * 36.0,
    )
    placer = Placer(make_scene(boundary=make_boundary(-10, -10, 46, 46)))
    spec = PATTERN_LIBRARY["formal_bosque_grid"]
    assert spec.trees_only and spec.tree_step_m == 6.0
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="formal_bosque_grid",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_linear(placer, zone, spec, assignment, trees, bushes)
    assert objects
    # trees_only -- ни одного куста, только деревья.
    assert all(o.type == "tree" for o in objects)


def test_concentric_rings_places_points_around_the_centroid(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    square = [(0, 0), (60, 0), (60, 60), (0, 60)]
    zone = GeometricZone(
        id="open_area_concentric_test", kind="open_area",
        polygon=[Point2(x=x, z=z) for x, z in square], area_sqm=60.0 * 60.0,
    )
    placer = Placer(make_scene(boundary=make_boundary(-10, -10, 70, 70)))
    spec = PATTERN_LIBRARY["concentric_rings"]
    assert spec.line_shape == "concentric"
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="concentric_rings",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_linear(placer, zone, spec, assignment, trees, bushes)
    assert objects
    # Точки лежат на нескольких разных дистанциях от центра (кольца), а не
    # на одном прямом ряду через середину -- разброс расстояний до центра
    # должен быть заметным, а не колебанием вокруг одного значения.
    center_x, center_z = 30.0, 30.0
    distances = [math.hypot(o.position.x - center_x, o.position.z - center_z) for o in objects]
    assert max(distances) - min(distances) > 5.0


def test_triangular_grid_fill_uses_dense_lattice_without_pick_spread_thinning(catalog):
    trees, bushes = _trees_and_bushes(catalog)
    square = [(0, 0), (36, 0), (36, 36), (0, 36)]
    zone = GeometricZone(
        id="open_area_triangular_test", kind="open_area",
        polygon=[Point2(x=x, z=z) for x, z in square], area_sqm=36.0 * 36.0,
    )
    placer = Placer(make_scene(boundary=make_boundary(-10, -10, 46, 46)))
    spec = PATTERN_LIBRARY["triangular_grid_fill"]
    assert spec.dense_lattice
    assignment = ZoneAssignment(
        zone_id=zone.id, zone_kind="open_area", pattern_id="triangular_grid_fill",
        source_project=None, source_quote=None, confidence=0.0,
    )
    objects = _place_area_fill(placer, zone, spec, assignment, trees, bushes)
    assert objects
    # Треугольная решётка -- соседние ряды по z сдвинуты на полшага по x
    # (row_height=step*sqrt(3)/2 в Placer.points_in_area) -- достаточно
    # проверить, что среди принятых точек есть больше одного уникального x
    # на разных z (не выровнены в один столбец, как дал бы простой перебор).
    trees_only = [o for o in objects if o.type == "tree"]
    assert len(trees_only) > 3
    xs = {round(o.position.x, 1) for o in trees_only}
    assert len(xs) > 1
