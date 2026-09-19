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

from deterministic_placement import generate_for_scene
from pattern_corpus import _corpus_scenes
from placement import OBJECT_CLEARANCE_M, POINT_CLEARANCE_M
from setback_norms import setback_for
from shapely.geometry import Point, Polygon
from zone_partitioning import partition_zones


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
