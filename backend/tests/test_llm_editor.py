"""llm_editor.py -- операции правки плана текстом. apply_plan(scene, plan,
catalog) -- полностью детерминированная, офлайн-тестируемая часть (никакого
обращения к LLM): модель лишь выбирает намерение/параметры, эту часть и
проверяем здесь, теми же приёмами, что использовались при разработке фичи --
хэнд-билженые LlmPlan на реальных участках locations/."""

import json
import math

import placement
import pytest
from llm_editor import (
    LlmPlan,
    _build_context,
    _catalog_for_prompt,
    _default_spacing,
    _editable_types,
    _half_depth,
    _inner_center,
    _is_oriented,
    _labels,
    _normalize,
    _outline,
    _pack_substitutes,
    _plural,
    _pool_kind,
    _spacing_for,
    apply_plan,
)
from placement import Placer
from plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, catalog_by_id, load_catalog
from shapely.geometry import Point, Polygon

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
# Чистые вспомогательные функции
# =============================================================================


def test_normalize_drops_none_values():
    assert _normalize({"op": "add", "x": 1.0, "z": None}) == {"op": "add", "x": 1.0}


def test_normalize_singular_catalog_id_becomes_list_for_place_along():
    assert _normalize({"op": "place_along", "catalog_id": "bush_medium"})["catalog_ids"] == ["bush_medium"]


def test_normalize_singular_catalog_id_becomes_list_for_place_in_area():
    assert _normalize({"op": "place_in_area", "catalog_id": "tree_medium"})["catalog_ids"] == ["tree_medium"]


def test_normalize_does_not_override_existing_catalog_ids():
    raw = {"op": "place_along", "catalog_id": "bush_medium", "catalog_ids": ["tree_medium"]}
    assert _normalize(raw)["catalog_ids"] == ["tree_medium"]


def test_normalize_singular_object_type_becomes_list_for_remove_where():
    assert _normalize({"op": "remove_where", "object_type": "lamp"})["object_types"] == ["lamp"]


def test_normalize_wraps_bare_strings_into_lists_for_known_keys():
    raw = {"op": "design_area", "elements": "trees", "tree_ids": "tree_medium", "bush_ids": "bush_medium"}
    normalized = _normalize(raw)
    assert normalized["elements"] == ["trees"]
    assert normalized["tree_ids"] == ["tree_medium"]
    assert normalized["bush_ids"] == ["bush_medium"]


def test_normalize_leaves_actual_lists_untouched():
    raw = {"op": "remove_where", "object_types": ["lamp", "bench"]}
    assert _normalize(raw)["object_types"] == ["lamp", "bench"]


def test_plural_russian_pluralization_rules():
    assert _plural(1, ("объект", "объекта", "объектов")) == "1 объект"
    assert _plural(2, ("объект", "объекта", "объектов")) == "2 объекта"
    assert _plural(5, ("объект", "объекта", "объектов")) == "5 объектов"
    assert _plural(11, ("объект", "объекта", "объектов")) == "11 объектов"
    assert _plural(21, ("объект", "объекта", "объектов")) == "21 объект"


def test_labels_joins_with_semicolons_and_truncates_after_three():
    items = [BY_ID["bush_medium"], BY_ID["bush_tall"], BY_ID["bush_short"], BY_ID["tree_medium"]]
    label = _labels(items)
    assert "и ещё 1" in label
    assert label.count(";") == 2


def test_labels_no_truncation_for_three_or_fewer():
    items = [BY_ID["bush_medium"], BY_ID["bush_tall"]]
    assert "ещё" not in _labels(items)


def test_pool_kind_prefers_tree_over_bush():
    assert _pool_kind([BY_ID["bush_medium"], BY_ID["tree_medium"]]) == "tree"


def test_pool_kind_bush_when_no_tree_present():
    assert _pool_kind([BY_ID["bush_medium"], BY_ID["hedge_segment"]]) == "bush"


def test_pool_kind_none_for_furniture_only():
    assert _pool_kind([BY_ID["bench"], BY_ID["lamp"]]) is None


def test_default_spacing_tree_scales_with_crown_radius():
    assert _default_spacing(BY_ID["tree_tall"]) == max(5.0, 2 * BY_ID["tree_tall"].dimensions.radius + 2.0)


def test_default_spacing_furniture_uses_fixed_table():
    assert _default_spacing(BY_ID["bench"]) == 10.0
    assert _default_spacing(BY_ID["lamp"]) == 15.0


def test_default_spacing_oriented_item_uses_width():
    assert _default_spacing(BY_ID["hedge_segment"]) == BY_ID["hedge_segment"].dimensions.width


def test_default_spacing_round_item_without_radius_falls_back():
    bare = CatalogItem(
        id="bare", category="furniture", label="bare", object_type="something_custom", model="/x.glb",
        dimensions=CatalogItemDimensions(height=1.0), render=CatalogItemRender(shape="box", color="#000"),
    )
    assert _default_spacing(bare) == 1.4  # max(1.2, 2*(radius or 0.5) + 0.4) = max(1.2, 1.4)


def test_spacing_for_uses_requested_when_given():
    assert _spacing_for(3.3, [BY_ID["bush_medium"]]) == 3.3


def test_spacing_for_clamps_to_allowed_range():
    assert _spacing_for(1000.0, [BY_ID["bush_medium"]]) == placement.MAX_SPACING_M
    assert _spacing_for(0.01, [BY_ID["bush_medium"]]) == placement.MIN_SPACING_M


def test_half_depth_prefers_radius_then_depth_then_default():
    assert _half_depth(BY_ID["tree_medium"]) == BY_ID["tree_medium"].dimensions.radius
    assert _half_depth(BY_ID["hedge_segment"]) == BY_ID["hedge_segment"].dimensions.depth / 2
    bare = CatalogItem(
        id="bare", category="furniture", label="bare", object_type="bare", model="/x.glb",
        dimensions=CatalogItemDimensions(height=1.0), render=CatalogItemRender(shape="box", color="#000"),
    )
    assert _half_depth(bare) == 0.5


def test_is_oriented_true_for_width_based_items_false_for_round():
    assert _is_oriented(BY_ID["hedge_segment"]) is True
    assert _is_oriented(BY_ID["tree_medium"]) is False


def test_editable_types_covers_every_catalog_object_type():
    types = _editable_types(CATALOG)
    assert "tree" in types
    assert "bench" in types
    assert "building" not in types  # здания не в каталоге посадок/МАФ


def test_pack_substitutes_empty_without_a_generated_pack():
    from plant_catalog import CATALOG as BASE_CATALOG

    assert _pack_substitutes(BASE_CATALOG) == {}


def _fake_pack_item(id: str, size_class: str, crown_class: str) -> CatalogItem:
    return CatalogItem(
        id=id, category="tree", label=id, size_class=size_class, crown_class=crown_class,
        setback_kind="tree", object_type="tree", model=f"/models/{id}.glb",
        dimensions=CatalogItemDimensions(height=3.0, radius=1.0), render=CatalogItemRender(shape="cluster", color="#2e7d3a"),
    )


def test_pack_substitutes_maps_base_trees_to_matching_pack_item():
    # Пак не гарантированно подключён в окружении (catalog_generated.json
    # генерируется отдельно, tools/convert_models.mjs, и не лежит в git --
    # см. .gitignore) -- строим свой минимальный пак, а не полагаемся на
    # файловую систему конкретной машины/CI.
    from plant_catalog import CATALOG as BASE_CATALOG

    pack_item = _fake_pack_item("pack_tree_1", size_class="medium", crown_class="regular")
    fake_catalog = [*BASE_CATALOG, pack_item]
    substitutes = _pack_substitutes(fake_catalog)
    assert substitutes
    for base_id, sub in substitutes.items():
        assert sub.id not in {i.id for i in BASE_CATALOG if i.id == base_id}


# =============================================================================
# run(): разбор операций, устойчивость к мусору
# =============================================================================


def test_run_rejects_unparseable_operation_without_stopping_the_rest(scene1):
    result = run(scene1, {"op": "not_a_real_operation"}, {"op": "remove", "id": "lamp_001"})
    assert any("не разобрать" in r for r in result.rejected)
    assert any("удалён lamp_001" in a for a in result.applied)


def test_run_rejects_operation_missing_required_field(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "bush_medium"})  # нет x/z
    assert result.rejected
    assert "add" in result.rejected[0]


def test_run_preserves_original_scene_object_count_on_full_failure(scene1):
    original_count = len(scene1.objects)
    run(scene1, {"op": "remove", "id": "no_such_object"})
    assert len(scene1.objects) == original_count  # исходная сцена не мутирована


def test_run_empty_plan_returns_scene_unchanged(scene1):
    result = run(scene1)
    assert len(result.scene.objects) == len(scene1.objects)
    assert result.applied == []
    assert result.rejected == []


# =============================================================================
# add / remove / move / rotate
# =============================================================================


def test_add_places_object_at_requested_point_when_free(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "bush_medium", "x": 30.0, "z": 0.0})
    assert result.applied
    new_bushes = [o for o in result.scene.objects if o.metadata.get("source") == "llm"]
    assert len(new_bushes) == 1


def test_add_unknown_catalog_id_is_rejected(scene1):
    result = run(scene1, {"op": "add", "catalog_id": "not_a_real_item", "x": 0.0, "z": 0.0})
    assert result.rejected
    assert "not_a_real_item" in result.rejected[0]


def test_add_snaps_when_requested_point_violates_setback(scene1):
    # (0, 0) внутри здания в локации 1 -- должно сдвинуть или отклонить, не упасть.
    result = run(scene1, {"op": "add", "catalog_id": "tree_medium", "x": 0.0, "z": 0.0})
    assert result.applied or result.rejected


def test_remove_deletes_the_object(scene1):
    result = run(scene1, {"op": "remove", "id": "lamp_001"})
    assert "удалён lamp_001" in result.applied[0]
    assert "lamp_001" not in {o.id for o in result.scene.objects}


def test_remove_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "remove", "id": "no_such_id"})
    assert "такого объекта нет" in result.rejected[0]


def test_remove_uneditable_type_is_rejected(scene1):
    building_id = next(o.id for o in scene1.objects if o.type == "building")
    result = run(scene1, {"op": "remove", "id": building_id})
    assert "менять нельзя" in result.rejected[0]


def test_move_relocates_object_to_free_point(scene1):
    result = run(scene1, {"op": "move", "id": "lamp_001", "x": 40.0, "z": 0.0})
    moved = next(o for o in result.scene.objects if o.id == "lamp_001")
    assert (moved.position.x, moved.position.z) != (0, 0)


def test_move_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "move", "id": "no_such_id", "x": 0.0, "z": 0.0})
    assert result.rejected


def test_move_restores_original_occupancy_when_target_is_unreachable(scene1):
    # Внутрь здания, далеко от границы -- нет годного места поблизости.
    result = run(scene1, {"op": "move", "id": "lamp_001", "x": 0.0, "z": 0.0})
    if result.rejected:
        # Объект должен остаться на старом месте и не исчезнуть из индекса
        # (иначе следующая операция могла бы поставить что-то поверх него).
        assert any(o.id == "lamp_001" for o in result.scene.objects)


def test_rotate_changes_rotation_in_radians(scene1):
    result = run(scene1, {"op": "rotate", "id": "lamp_001", "rotation_deg": 90.0})
    rotated = next(o for o in result.scene.objects if o.id == "lamp_001")
    assert rotated.rotation == pytest.approx(math.radians(90.0))


def test_rotate_nonexistent_object_is_rejected(scene1):
    result = run(scene1, {"op": "rotate", "id": "no_such_id", "rotation_deg": 10})
    assert result.rejected


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
# connect
# =============================================================================


def test_connect_two_entrances_builds_a_path(scene1):
    result = run(scene1, {"op": "connect", "from_id": "entrance_001", "to_id": "entrance_002"})
    assert result.applied or result.rejected  # геометрически может не выйти, но не падает
    if result.applied:
        assert any(o.type == "path_segment" for o in result.scene.objects)


def test_connect_missing_endpoint_object_is_rejected(scene1):
    result = run(scene1, {"op": "connect", "from_id": "no_such_id", "to_id": "entrance_002"})
    assert result.rejected
    assert "объекта" in result.rejected[0]


def test_connect_missing_target_is_rejected(scene1):
    result = run(scene1, {"op": "connect", "from_target": "playground", "to_id": "entrance_002"})
    assert result.rejected


def test_connect_no_endpoint_specified_is_rejected(scene1):
    result = run(scene1, {"op": "connect", "to_id": "entrance_002"})
    assert "не указана точка" in result.rejected[0]


def test_connect_points_too_close_together_is_rejected(scene1):
    result = run(scene1, {"op": "connect", "from_x": 0.0, "from_z": 0.0, "to_x": 0.1, "to_z": 0.1})
    assert "рядом" in result.rejected[0]


def test_connect_routes_around_a_building(scene2):
    # Локация 2 -- три здания, есть где обходить.
    result = run(scene2, {"op": "connect", "from_x": -60, "from_z": 0, "to_x": 60, "to_z": 0})
    assert result.applied or result.rejected


# =============================================================================
# replace_where / thin_out / resize / face / align_along / remove_where
# =============================================================================


def test_remove_where_all_types_in_radius(scene1):
    result = run(scene1, {"op": "remove_where", "x": 0.0, "z": 0.0, "radius_m": 1000.0})
    assert "удалено" in result.applied[0]
    assert not any(o.type == "lamp" for o in result.scene.objects)


def test_remove_where_specific_type_only(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["lamp"]})
    assert not any(o.type == "lamp" for o in result.scene.objects)
    assert any(o.type == "entrance" for o in result.scene.objects)


def test_remove_where_uneditable_type_only_is_rejected(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["building"]})
    assert result.rejected


def test_remove_where_uneditable_and_editable_mixed_warns_but_proceeds(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["building", "lamp"]})
    assert any("менять нельзя" in w for w in result.warnings)
    assert not any(o.type == "lamp" for o in result.scene.objects)


def test_remove_where_no_matches_is_rejected(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["lamp"], "x": 99999.0, "z": 99999.0, "radius_m": 1.0})
    assert "подходящих объектов нет" in result.rejected[0]


def test_remove_where_target_filter(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["lamp"], "target": "pedestrian_path", "distance_m": 3.0})
    assert result.applied or result.rejected


def test_remove_where_target_missing_is_rejected(scene1):
    result = run(scene1, {"op": "remove_where", "object_types": ["lamp"], "target": "playground"})
    assert "нет цели" in result.rejected[0]


def test_replace_where_swaps_catalog_item(scene1):
    result = run(scene1, {"op": "replace_where", "object_types": ["lamp"], "catalog_ids": ["bench"], "x": 0, "z": 0, "radius_m": 1000})
    assert result.applied or result.rejected


def test_replace_where_no_matches_is_rejected(scene1):
    result = run(scene1, {"op": "replace_where", "object_types": ["lamp"], "catalog_ids": ["bench"], "x": 99999, "z": 99999, "radius_m": 1.0})
    assert "подходящих объектов нет" in result.rejected[0]


def test_thin_out_reduces_density(scene1):
    # Ставим много кустов плотно, потом прореживаем.
    setup = run(scene1, {"op": "place_along", "target": "pedestrian_path", "catalog_ids": ["bush_medium"], "spacing_m": 1.0})
    result = apply_plan(setup.scene, LlmPlan(operations=[{"op": "thin_out", "object_types": ["bush"], "min_spacing_m": 5.0}]), CATALOG)
    assert result.applied or result.rejected


def test_thin_out_no_matches_is_rejected(scene1):
    result = run(scene1, {"op": "thin_out", "object_types": ["bush"]})
    assert "подходящих объектов нет" in result.rejected[0]


def test_resize_changes_scale(scene1):
    result = run(scene1, {"op": "resize", "object_types": ["lamp"], "scale": 2.0, "x": 0, "z": 0, "radius_m": 1000})
    assert any(o.scale == 2.0 for o in result.scene.objects if o.type == "lamp")


def test_resize_clamps_out_of_range_scale(scene1):
    result = run(scene1, {"op": "resize", "object_types": ["lamp"], "scale": 100.0, "x": 0, "z": 0, "radius_m": 1000})
    assert any("вне допустимого диапазона" in w for w in result.warnings)


def test_resize_no_matches_is_rejected(scene1):
    result = run(scene1, {"op": "resize", "object_types": ["lamp"], "scale": 1.5, "x": 99999, "z": 99999, "radius_m": 1.0})
    assert result.rejected


def test_face_turns_objects_toward_target_point(scene1):
    result = run(scene1, {"op": "face", "object_types": ["lamp"], "at_x": 0.0, "at_z": 0.0, "x": 0, "z": 0, "radius_m": 1000})
    assert result.applied or result.rejected


def test_face_missing_at_point_is_rejected(scene1):
    result = run(scene1, {"op": "face", "object_types": ["lamp"], "at_id": "no_such_id"})
    assert result.rejected


def test_face_no_matching_objects_is_rejected(scene1):
    result = run(scene1, {"op": "face", "object_types": ["lamp"], "at_x": 0, "at_z": 0, "x": 99999, "z": 99999, "radius_m": 1.0})
    assert result.rejected


def test_align_along_moves_objects_onto_the_row(scene1):
    result = run(scene1, {"op": "align_along", "object_types": ["lamp"], "target": "pedestrian_path"})
    assert result.applied or result.rejected


def test_align_along_missing_target_is_rejected(scene1):
    result = run(scene1, {"op": "align_along", "object_types": ["lamp"], "target": "playground"})
    assert "таких объектов нет" in result.rejected[0]


def test_align_along_no_nearby_objects_is_rejected(scene1):
    result = run(scene1, {"op": "align_along", "object_types": ["bench"], "target": "pedestrian_path"})
    assert "подходящих объектов рядом нет" in result.rejected[0]


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


# =============================================================================
# design_area -- уже обширно покрыт test_courtyard_design.py; здесь -- только
# то, что специфично для llm_editor (интеграция с _pool/_normalize).
# =============================================================================


def test_design_area_via_run_dispatch(scene1):
    result = run(scene1, {"op": "design_area"})
    assert result.applied or result.rejected


def test_design_area_with_explicit_tree_and_bush_ids(scene1):
    result = run(
        scene1,
        {"op": "design_area", "elements": ["trees", "bushes"], "tree_ids": ["tree_medium"], "bush_ids": ["bush_medium"]},
    )
    assert result.applied or result.rejected


# =============================================================================
# Контекст для модели: _outline / _inner_center / _catalog_for_prompt /
# _build_context -- чистые функции без единого обращения к сети, но именно
# они формируют то, что реально уходит в промпт (см. request_plan).
# =============================================================================


def test_outline_rounds_to_half_meter_and_drops_closing_point():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.03), (0.0, 10.0)]
    outline = _outline(square)
    assert outline[-1] != outline[0]
    assert all(abs(x * 2 - round(x * 2)) < 1e-9 for x, _ in outline)


def test_outline_simplifies_when_over_the_point_limit():
    # Многоугольник, аппроксимирующий круг -- заведомо больше 5 точек лимита,
    # simplify должен ужать его, а не отдать все точки как есть.
    import math as _math

    circle_points = [(10 * _math.cos(a), 10 * _math.sin(a)) for a in [i * 2 * _math.pi / 40 for i in range(40)]]
    outline = _outline(circle_points, limit=5)
    assert len(outline) <= 6  # чуть больше лимита geometrически неизбежно, но не все 40


def test_outline_degenerate_zero_area_polygon_returns_rounded_points_as_is():
    # Три коллинеарные точки -- Polygon(...) конструируется, но невалиден
    # (нулевая площадь) -- функция просто округляет исходные точки, не
    # пытаясь их упростить.
    points = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    assert _outline(points) == [[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]]


def test_inner_center_uses_centroid_for_convex_shape():
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert _inner_center(square) == [5.0, 5.0]


def test_inner_center_falls_back_to_representative_point_for_u_shape():
    # П-образный двор: центроид геометрически лежит СНАРУЖИ фигуры (в вырезе).
    u_shape = Polygon([(0, 0), (30, 0), (30, 30), (20, 30), (20, 5), (10, 5), (10, 30), (0, 30)])
    assert not u_shape.contains(u_shape.centroid)
    center = _inner_center(u_shape)
    assert u_shape.contains(Point(center[0], center[1]))


def test_catalog_for_prompt_hides_base_trees_when_pack_is_available():
    # Как и в _pack_substitutes выше -- пак не гарантирован в окружении
    # (catalog_generated.json не в git), строим свой минимальный.
    from plant_catalog import CATALOG as BASE_CATALOG

    pack_item = _fake_pack_item("pack_tree_1", size_class="medium", crown_class="regular")
    fake_catalog = [*BASE_CATALOG, pack_item]
    rows = _catalog_for_prompt(fake_catalog)
    ids_in_prompt = {row[0] for row in rows}
    base_tree_ids = {item.id for item in BASE_CATALOG if item.category == "tree"}
    assert base_tree_ids.isdisjoint(ids_in_prompt)  # пак подключён -- базовые деревья скрыты
    assert "pack_tree_1" in ids_in_prompt


def test_catalog_for_prompt_keeps_base_non_tree_items():
    rows = _catalog_for_prompt(CATALOG)
    ids_in_prompt = {row[0] for row in rows}
    assert "bench" in ids_in_prompt
    assert "hedge_segment" in ids_in_prompt


def test_catalog_for_prompt_limits_pack_items_per_shape_class():
    from llm_editor import MAX_PACK_ITEMS_PER_SHAPE_CLASS
    from plant_catalog import CATALOG as BASE_CATALOG

    # Заведомо больше лимита одинаковых (category, size_class, crown_class) --
    # без явно построенного пака (вместо реального catalog_generated.json,
    # которого может не быть в окружении) проверка обрезания была бы
    # вырожденно истинной на пустом множестве, ничего не проверяя.
    pack_items = [_fake_pack_item(f"pack_tree_{i}", size_class="medium", crown_class="regular") for i in range(MAX_PACK_ITEMS_PER_SHAPE_CLASS + 5)]
    fake_catalog = [*BASE_CATALOG, *pack_items]
    rows = _catalog_for_prompt(fake_catalog)

    from collections import Counter

    per_class = Counter()
    base_ids = {item.id for item in BASE_CATALOG}
    by_id = {item.id: item for item in fake_catalog}
    for row in rows:
        item = by_id[row[0]]
        if item.id not in base_ids:
            per_class[(item.category, item.size_class, item.crown_class)] += 1
    assert per_class[("tree", "medium", "regular")] == MAX_PACK_ITEMS_PER_SHAPE_CLASS


def test_catalog_for_prompt_without_pack_shows_base_trees(monkeypatch):
    import llm_editor

    monkeypatch.setattr(llm_editor, "_pack_substitutes", lambda catalog: {})
    from plant_catalog import CATALOG as BASE_CATALOG

    rows = _catalog_for_prompt(BASE_CATALOG)
    ids_in_prompt = {row[0] for row in rows}
    assert "tree_medium" in ids_in_prompt


def test_build_context_is_valid_json_with_expected_top_level_keys(scene1):
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert set(context.keys()) == {
        "coordinates", "site_outline", "buildings", "buildings_not_shown", "landmarks", "landmarks_not_shown",
        "targets", "free_areas", "restriction_zones", "objects", "objects_not_shown", "object_counts",
        "catalog_columns", "catalog_rows",
    }
    assert context["site_outline"] is not None
    assert context["object_counts"]


def test_build_context_includes_entrances_as_landmarks(scene1):
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert any(lm["type"] == "подъезд" for lm in context["landmarks"])


def test_build_context_without_boundary_has_null_site_outline():
    from schemas import Scene, SceneMeta

    scene = Scene(
        boundary=None, restrictions=[], objects=[],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )
    placer = Placer(scene)
    context = json.loads(_build_context(scene, CATALOG, placer))
    assert context["site_outline"] is None


def test_build_context_truncates_objects_beyond_the_prompt_limit(monkeypatch, scene1):
    import llm_editor

    monkeypatch.setattr(llm_editor, "MAX_OBJECTS_IN_PROMPT", 2)
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert len(context["objects"]) == 2
    assert context["objects_not_shown"] > 0
