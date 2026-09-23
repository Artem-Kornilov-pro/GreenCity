"""text_editor/prompt.py -- контекст участка для модели: _outline, _inner_center,
_catalog_for_prompt, _build_context."""

import json

import pytest
from shapely.geometry import Point, Polygon

from core.placement import Placer
from core.plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, catalog_by_id, load_catalog
from text_editor.prompt import (
    _build_context,
    _catalog_for_prompt,
    _inner_center,
    _outline,
)
from text_editor.service import LlmPlan, apply_plan

CATALOG = load_catalog()
BY_ID = catalog_by_id()


def _fake_pack_item(id: str, size_class: str, crown_class: str) -> CatalogItem:
    return CatalogItem(
        id=id, category="tree", label=id, size_class=size_class, crown_class=crown_class,
        setback_kind="tree", object_type="tree", model=f"/models/{id}.glb",
        dimensions=CatalogItemDimensions(height=3.0, radius=1.0), render=CatalogItemRender(shape="cluster", color="#2e7d3a"),
    )


@pytest.fixture
def scene1(location_scene):
    return location_scene[1].model_copy(deep=True)


@pytest.fixture
def scene2(location_scene):
    return location_scene[2].model_copy(deep=True)


def run(scene, *operations, catalog=CATALOG):
    return apply_plan(scene, LlmPlan(operations=list(operations)), catalog)


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
    from core.plant_catalog import CATALOG as BASE_CATALOG

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
    from core.plant_catalog import CATALOG as BASE_CATALOG
    from text_editor.prompt import MAX_PACK_ITEMS_PER_SHAPE_CLASS

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
    from text_editor import prompt

    monkeypatch.setattr(prompt, "_pack_substitutes", lambda catalog: {})
    from core.plant_catalog import CATALOG as BASE_CATALOG

    rows = _catalog_for_prompt(BASE_CATALOG)
    ids_in_prompt = {row[0] for row in rows}
    assert "tree_medium" in ids_in_prompt


def test_build_context_is_valid_json_with_expected_top_level_keys(scene1):
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert set(context.keys()) == {
        "coordinates", "site_outline", "buildings", "buildings_not_shown", "landmarks", "landmarks_not_shown",
        "targets", "free_areas", "selected_areas", "restriction_zones", "objects", "objects_not_shown",
        "object_counts", "catalog_columns", "catalog_rows",
    }
    assert context["site_outline"] is not None
    assert context["object_counts"]


def test_build_context_includes_a_manually_selected_area_by_name(scene1):
    from core.schemas import Point2, RestrictionZone

    zone = RestrictionZone(
        id="selection_1",
        type="selection",
        name="Выделение",
        polygon=[Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=5, z=5), Point2(x=0, z=5)],
        severity="allowed",
        minDistance=0,
        message="Зона, выделенная вручную",
    )
    scene = scene1.model_copy(update={"restrictions": [*scene1.restrictions, zone]})
    placer = Placer(scene)
    context = json.loads(_build_context(scene, CATALOG, placer))
    assert context["selected_areas"] == [{"name": "Выделение", "outline": [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [0.0, 5.0]]}]
    # severity "allowed" -- просто маркер, в restriction_zones (только не-allowed) не попадает.
    assert "selection" not in context["restriction_zones"]


def test_build_context_selected_areas_empty_without_allowed_zones(scene1):
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert context["selected_areas"] == []


def test_build_context_includes_entrances_as_landmarks(scene1):
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert any(lm["type"] == "подъезд" for lm in context["landmarks"])


def test_build_context_without_boundary_has_null_site_outline():
    from core.schemas import Scene, SceneMeta

    scene = Scene(
        boundary=None, restrictions=[], objects=[],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )
    placer = Placer(scene)
    context = json.loads(_build_context(scene, CATALOG, placer))
    assert context["site_outline"] is None


def test_build_context_truncates_objects_beyond_the_prompt_limit(monkeypatch, scene1):
    from text_editor import prompt

    monkeypatch.setattr(prompt, "MAX_OBJECTS_IN_PROMPT", 2)
    placer = Placer(scene1)
    context = json.loads(_build_context(scene1, CATALOG, placer))
    assert len(context["objects"]) == 2
    assert context["objects_not_shown"] > 0
