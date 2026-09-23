"""text_editor/ops_placement.py -- групповые посадки правки текстом: place_along,
place_in_area, cover_area, line_of, enclose, duplicate_near, set_count (и define_zone)."""

import math

import pytest
from shapely.geometry import Point, Polygon

from core.plant_catalog import catalog_by_id, load_catalog
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()
BY_ID = catalog_by_id()


@pytest.fixture
def scene1(location_scene):
    return location_scene[1].model_copy(deep=True)


@pytest.fixture
def scene2(location_scene):
    return location_scene[2].model_copy(deep=True)


def run(scene, *operations, catalog=CATALOG):
    return apply_plan(scene, LlmPlan(operations=list(operations)), catalog)


# =============================================================================
# place_along
# =============================================================================


def test_place_along_pedestrian_path_adds_objects(scene1):
    result = run(scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium"]})
    assert result.applied
    assert any(o.metadata.get("source") == "llm" for o in result.scene.objects)


def test_place_along_missing_target_is_rejected(scene1):
    result = run(scene1, {"op": "place_along", "target": "playground", "catalog_ids": ["bush_medium"]})
    assert "таких объектов нет" in result.rejected[0]


def test_place_along_unknown_catalog_id_is_rejected(scene1):
    result = run(scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["nope"]})
    assert result.rejected


def test_place_along_respects_max_count(scene1):
    result = run(
        scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium"], "max_count": 2}
    )
    new_objs = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert len(new_objs) <= 2


def test_place_along_max_count_below_one_is_rejected(scene1):
    result = run(scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium"], "max_count": 0})
    assert "max_count" in result.rejected[0]


def test_place_along_restricts_to_a_circle_when_x_z_given(scene1):
    result = run(
        scene1,
        {
            "op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium"],
            "x": 0.0, "z": 0.0, "radius_m": 15.0,
        },
    )
    for obj in result.scene.objects:
        if obj.metadata.get("source") == "llm":
            assert math.hypot(obj.position.x, obj.position.z) <= 15.0 + 1e-6


def test_place_along_hedge_checks_both_ends_of_the_section(scene1):
    result = run(scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["hedge_segment"]})
    assert result.applied or result.rejected  # не падает на вытянутых объектах


# =============================================================================
# place_in_area
# =============================================================================


def test_place_in_area_around_point(scene1):
    result = run(
        scene1, {"op": "place_in_area", "catalog_ids": ["bush_medium"], "count": 3, "x": 30.0, "z": 0.0}
    )
    assert result.applied
    new_objs = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert 0 < len(new_objs) <= 3


def test_place_in_area_spread_over_whole_site(scene1):
    result = run(scene1, {"op": "place_in_area", "catalog_ids": ["tree_medium"], "count": 5})
    assert result.applied


def test_place_in_area_defaults_count_to_five_when_model_omits_it(scene1):
    """Просьба вида "посади разные виды деревьев" называет только
    разнообразие, без числа -- модель иногда пропускает count целиком, и
    раньше вся операция отклонялась с "count: Field required", хотя
    explanation модель всё равно писала так, будто посадка удалась."""
    result = run(scene1, {"op": "place_in_area", "catalog_ids": ["tree_medium", "tree_tall"]})
    assert not result.rejected
    new_trees = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert 0 < len(new_trees) <= 5


def test_place_in_area_count_below_one_is_rejected(scene1):
    result = run(scene1, {"op": "place_in_area", "catalog_ids": ["bush_medium"], "count": 0})
    assert "count" in result.rejected[0]


def test_place_in_area_unknown_area_id_is_rejected(scene1):
    result = run(scene1, {"op": "place_in_area", "catalog_ids": ["bush_medium"], "count": 2, "area": "no-such-zone"})
    assert "нет свободной области" in result.rejected[0]


def test_place_in_area_named_zone_from_define_zone(scene1):
    result = run(
        scene1,
        {"op": "define_zone", "name": "Тестовая зона", "x": 30.0, "z": 0.0, "radius_m": 15.0, "severity": "allowed"},
        {"op": "place_in_area", "catalog_ids": ["bush_medium"], "count": 2, "area": "Тестовая зона"},
    )
    assert any("Тестовая зона" in a for a in result.applied)


def test_place_in_area_named_zone_keeps_full_crown_inside_not_just_center(scene1):
    """Раньше проверялась только точка-центр посадки, а не крона: дерево с
    центром у самого края маленькой именованной зоны (define_zone или
    выделение мышкой -- сырой полигон без единого отступа, в отличие от
    free_areas) визуально вылезало за её границу."""
    item = catalog_by_id()["tree_medium"]
    crown_radius = item.dimensions.radius
    zone_center = Point(30.0, 0.0)
    zone_radius = 6.0
    result = run(
        scene1,
        {"op": "define_zone", "name": "Маленькая зона", "x": 30.0, "z": 0.0, "radius_m": zone_radius, "severity": "allowed"},
        {"op": "place_in_area", "catalog_ids": ["tree_medium"], "count": 30, "area": "Маленькая зона"},
    )
    zone_poly = zone_center.buffer(zone_radius)
    new_trees = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert new_trees
    for tree in new_trees:
        crown = Point(tree.position.x, tree.position.z).buffer(crown_radius)
        assert crown.within(zone_poly), f"крона дерева в ({tree.position.x}, {tree.position.z}) выходит за зону"


# =============================================================================
# cover_area
# =============================================================================


def test_cover_area_covers_around_a_point(scene1):
    result = run(scene1, {"op": "cover_area", "catalog_ids": ["lawn_patch"], "x": 30.0, "z": 0.0, "radius_m": 10.0})
    assert result.applied
    assert any(o.type == "lawn_patch" for o in result.scene.objects)


def test_cover_area_whole_site(scene1):
    result = run(scene1, {"op": "cover_area", "catalog_ids": ["lawn_patch"]})
    assert result.applied or result.rejected


def test_cover_area_unknown_catalog_id_is_rejected(scene1):
    result = run(scene1, {"op": "cover_area", "catalog_ids": ["nope"]})
    assert result.rejected


def test_cover_area_tiles_pack_almost_edge_to_edge_without_gross_overlap(scene1):
    # Кандидаты берутся по гексагональной сетке (Placer.points_in_area) с
    # шагом 0.95*width -- соседние плитки in соседних сдвинутых рядах могут
    # чуть перекрываться по углу (не идеальная прямоугольная раскладка), но
    # не заходить друг в друга на сколько-нибудь заметную долю площади.
    result = run(scene1, {"op": "cover_area", "catalog_ids": ["lawn_patch"], "x": 30.0, "z": 0.0, "radius_m": 15.0})
    tiles = [o for o in result.scene.objects if o.type == "lawn_patch"]
    assert tiles
    half = BY_ID["lawn_patch"].dimensions.width / 2
    tile_area = (2 * half) ** 2
    boxes = []
    for t in tiles:
        boxes.append(Polygon([
            (t.position.x - half, t.position.z - half), (t.position.x + half, t.position.z - half),
            (t.position.x + half, t.position.z + half), (t.position.x - half, t.position.z + half),
        ]))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a.intersection(b).area < 0.1 * tile_area


# =============================================================================
# line_of / enclose / duplicate_near / set_count / define_zone
# =============================================================================


def test_line_of_places_a_row_between_two_points(scene1):
    result = run(scene1, {"op": "line_of", "catalog_ids": ["bush_medium"], "from_x": -20, "from_z": 0, "to_x": 20, "to_z": 0})
    assert result.applied or result.rejected


def test_line_of_endpoints_too_close_is_rejected(scene1):
    result = run(scene1, {"op": "line_of", "catalog_ids": ["bush_medium"], "from_x": 0, "from_z": 0, "to_x": 0.1, "to_z": 0.1})
    assert "рядом" in result.rejected[0]


def test_line_of_by_object_ids(scene1):
    result = run(scene1, {"op": "line_of", "catalog_ids": ["bush_medium"], "from_id": "entrance_001", "to_id": "entrance_003"})
    assert result.applied or result.rejected


def test_enclose_around_a_point(scene1):
    result = run(scene1, {"op": "enclose", "catalog_ids": ["hedge_segment"], "around_x": 30.0, "around_z": 0.0, "radius_m": 8.0})
    assert result.applied or result.rejected


def test_enclose_around_a_target(scene1):
    result = run(scene1, {"op": "enclose", "catalog_ids": ["bush_medium"], "around_target": "pedestrian_path"})
    assert result.applied or result.rejected


def test_enclose_missing_center_is_rejected(scene1):
    result = run(scene1, {"op": "enclose", "catalog_ids": ["bush_medium"], "around_id": "no_such_id"})
    assert result.rejected


def test_duplicate_near_copies_object(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "bench", "x": 30.0, "z": 0.0})
    new_id = next(o.id for o in result.scene.objects if o.metadata.get("source") == "llm")
    result2 = apply_plan(result.scene, LlmPlan(operations=[{"op": "duplicate_near", "id": new_id, "near_x": 40.0, "near_z": 0.0, "count": 2}]), CATALOG)
    assert result2.applied or result2.rejected


def test_duplicate_near_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "duplicate_near", "id": "no_such_id", "near_x": 0, "near_z": 0})
    assert result.rejected


def test_duplicate_near_object_not_from_catalog_is_rejected(scene1):
    result = run(scene1, {"op": "duplicate_near", "id": "lamp_001", "near_x": 30, "near_z": 30})
    assert "скопировать нечем" in result.rejected[0]


def test_set_count_adds_when_below_target(scene1):
    result = run(scene1, {"op": "set_count", "object_types": ["lamp"], "count": 100, "catalog_ids": ["lamp"]})
    assert result.applied
    assert "добавлено" in result.applied[0]


def test_set_count_removes_when_above_target(scene1):
    result = run(scene1, {"op": "set_count", "object_types": ["lamp"], "count": 1})
    remaining = [o for o in result.scene.objects if o.type == "lamp"]
    assert len(remaining) == 1


def test_set_count_no_change_when_already_matching(scene1):
    current = sum(1 for o in scene1.objects if o.type == "lamp")
    result = run(scene1, {"op": "set_count", "object_types": ["lamp"], "count": current})
    assert "без изменений" in result.applied[0]


def test_set_count_needs_catalog_ids_when_nothing_exists_yet(scene1):
    result = run(scene1, {"op": "set_count", "object_types": ["fountain"], "count": 2})
    assert "не указано, чем добавлять" in result.rejected[0]


def test_define_zone_creates_a_named_restriction_zone(scene1):
    result = run(scene1, {"op": "define_zone", "name": "Детская зона", "x": 30.0, "z": 0.0, "radius_m": 10.0})
    assert any("Детская зона" in a for a in result.applied)
    assert any(z.name == "Детская зона" for z in result.scene.restrictions)


def test_define_zone_requires_a_name(scene1):
    result = run(scene1, {"op": "define_zone", "name": "  ", "x": 0.0, "z": 0.0})
    assert "не указано имя" in result.rejected[0]


def test_define_zone_requires_a_location(scene1):
    result = run(scene1, {"op": "define_zone", "name": "Зона"})
    assert "не указано место" in result.rejected[0]


def test_define_zone_around_existing_object(scene1):
    result = run(scene1, {"op": "define_zone", "name": "У фонаря", "around_id": "lamp_001", "radius_m": 5.0})
    assert result.applied


def test_define_zone_is_usable_by_later_operation_in_same_plan(scene1):
    result = run(
        scene1,
        {"op": "define_zone", "name": "Сад", "x": 30.0, "z": 0.0, "radius_m": 15.0, "severity": "allowed"},
        {"op": "place_along", "target": "Сад", "catalog_ids": ["bush_medium"]},
    )
    assert not any("таких объектов нет" in r for r in result.rejected)
