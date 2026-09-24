"""text_editor/plan_common.py -- чистые вспомогательные функции применения плана."""


import pytest

from core import placement
from core.plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, catalog_by_id, load_catalog
from text_editor.plan_common import (
    _default_spacing,
    _half_depth,
    _is_oriented,
    _labels,
    _normalize,
    _plural,
    _pool_kind,
    _spacing_for,
)
from text_editor.prompt import _editable_types
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()
BY_ID = catalog_by_id()
TREE_ID = "species_lipa_melkolistnaya"


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
    items = [BY_ID["bush_medium"], BY_ID["bush_tall"], BY_ID["bush_short"], BY_ID[TREE_ID]]
    label = _labels(items)
    assert "и ещё 1" in label
    assert label.count(";") == 2


def test_labels_no_truncation_for_three_or_fewer():
    items = [BY_ID["bush_medium"], BY_ID["bush_tall"]]
    assert "ещё" not in _labels(items)


def test_pool_kind_prefers_tree_over_bush():
    assert _pool_kind([BY_ID["bush_medium"], BY_ID[TREE_ID]]) == "tree"


def test_pool_kind_bush_when_no_tree_present():
    assert _pool_kind([BY_ID["bush_medium"], BY_ID["hedge_segment"]]) == "bush"


def test_pool_kind_none_for_furniture_only():
    assert _pool_kind([BY_ID["bench"], BY_ID["lamp"]]) is None


def test_default_spacing_tree_scales_with_crown_radius():
    assert _default_spacing(BY_ID[TREE_ID]) == max(5.0, 2 * BY_ID[TREE_ID].dimensions.radius + 2.0)


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
    assert _half_depth(BY_ID[TREE_ID]) == BY_ID[TREE_ID].dimensions.radius
    assert _half_depth(BY_ID["hedge_segment"]) == BY_ID["hedge_segment"].dimensions.depth / 2
    bare = CatalogItem(
        id="bare", category="furniture", label="bare", object_type="bare", model="/x.glb",
        dimensions=CatalogItemDimensions(height=1.0), render=CatalogItemRender(shape="box", color="#000"),
    )
    assert _half_depth(bare) == 0.5


def test_is_oriented_true_for_width_based_items_false_for_round():
    assert _is_oriented(BY_ID["hedge_segment"]) is True
    assert _is_oriented(BY_ID[TREE_ID]) is False


def test_editable_types_covers_every_catalog_object_type():
    types = _editable_types(CATALOG)
    assert "tree" in types
    assert "bench" in types
    assert "building" not in types  # здания не в каталоге посадок/МАФ
