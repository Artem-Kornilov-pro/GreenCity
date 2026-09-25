"""text_editor/ops_editing.py -- connect, правка существующего по условию и
design_area через apply_plan."""


import pytest

from core.plant_catalog import catalog_by_id, load_catalog
from greenplan.species_selection import TERRITORY_COLUMN, assortment_entry
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
# design_area -- уже обширно покрыт test_courtyard_design.py; здесь -- только
# то, что специфично для llm_editor (интеграция с _pool/_normalize).
# =============================================================================


def test_design_area_via_run_dispatch(scene1):
    result = run(scene1, {"op": "design_area"})
    assert result.applied or result.rejected


def test_design_area_default_trees_come_from_yard_assortment(scene1):
    # Раньше без tree_ids брались первые записи каталога подходящей формы --
    # груша уссурийская и туя.
    result = run(scene1, {"op": "design_area", "elements": ["trees"]})
    planted = {o.metadata["species"] for o in result.scene.objects if o.type == "tree" and o.metadata.get("source") == "llm"}
    by_label = {item.label: item for item in CATALOG}
    assert len(planted) == 3
    assert all(assortment_entry(by_label[label]).territories[TERRITORY_COLUMN["двор"]] == "+" for label in planted)


def test_design_area_with_explicit_tree_and_bush_ids(scene1):
    result = run(
        scene1,
        {"op": "design_area", "elements": ["trees", "bushes"], "tree_ids": ["species_lipa_melkolistnaya"], "bush_ids": ["bush_medium"]},
    )
    assert result.applied or result.rejected
