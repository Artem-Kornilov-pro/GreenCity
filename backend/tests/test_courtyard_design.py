"""courtyard_design.py -- полный дизайн двора (design_area). Геометрия
достаточно сложна (MST, видимость подъездов, подбор площади), чтобы
осмысленно тестировать в основном через реальные участки locations/ --
чистые геометрические хелперы (_rotation/_samples/_footprint/_mst_edges/
_rect_sides/_main_directions) проверяются отдельно, точечно."""

import math

import courtyard_design as cd
import pytest
from courtyard_design import (
    DEFAULT_ELEMENTS,
    ELEMENTS,
    _lines,
    _main_directions,
    _mst_edges,
    _rect_sides,
    _rotation,
    _samples,
)
from llm_editor import LlmPlan, apply_plan
from plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, load_catalog
from setback_norms import setback_for
from shapely.geometry import LineString, MultiLineString, Point, Polygon

CATALOG = load_catalog()


# --- Чистые геометрические хелперы -------------------------------------------


def test_lines_extracts_linestring_and_linearring():
    ls = LineString([(0, 0), (1, 1)])
    assert _lines(ls) == [ls]
    ring = Polygon([(0, 0), (1, 0), (1, 1)]).exterior
    assert len(_lines(ring)) == 1


def test_lines_handles_none_and_empty():
    assert _lines(None) == []
    assert _lines(LineString()) == []


def test_lines_flattens_multilinestring():
    mls = MultiLineString([[(0, 0), (1, 0)], [(2, 2), (3, 3)]])
    assert len(_lines(mls)) == 2


def test_rotation_matches_placer_points_along_convention():
    assert _rotation(1.0, 0.0) == pytest.approx(0.0)
    assert _rotation(0.0, 0.0) == 0.0
    assert _rotation(0.0, -1.0) == pytest.approx(90.0)


def test_samples_yields_evenly_spaced_points_with_unit_tangent():
    line = LineString([(0, 0), (10, 0)])
    points = list(_samples(line, step=5.0))
    assert len(points) == 2
    for _x, _z, tx, tz in points:
        assert math.hypot(tx, tz) == pytest.approx(1.0)
        assert tz == pytest.approx(0.0)


def test_samples_empty_for_degenerate_line():
    assert list(_samples(LineString([(0, 0), (0, 0)]), step=1.0)) == []


def _hedge_item():
    return CatalogItem(
        id="hedge_segment", category="bush", label="Изгородь", setback_kind="bush", object_type="hedge_segment",
        model="/models/hedge_segment.glb", dimensions=CatalogItemDimensions(height=0.9, width=2.0, depth=0.6),
        render=CatalogItemRender(shape="box", color="#4a7a44"),
    )


def _tree_item():
    return CatalogItem(
        id="tree_medium", category="tree", label="Дерево", setback_kind="tree", object_type="tree",
        model="/models/tree.glb", dimensions=CatalogItemDimensions(height=3.0, radius=0.9),
        render=CatalogItemRender(shape="cone", color="#2e7d3a"),
    )


def test_footprint_rectangle_for_oriented_items():
    poly = cd._footprint(_hedge_item(), 0, 0, 0.0)
    assert poly.area == pytest.approx(2.0 * 0.6)


def test_footprint_circle_for_radius_items():
    circle = cd._footprint(_tree_item(), 0, 0, 0.0)
    assert circle.area == pytest.approx(math.pi * 0.9**2, rel=0.05)


def test_footprint_point_when_no_dimensions_at_all():
    bare = CatalogItem(
        id="bare", category="furniture", label="bare", object_type="bare", model="/x.glb",
        dimensions=CatalogItemDimensions(height=1.0), render=CatalogItemRender(shape="box", color="#000"),
    )
    result = cd._footprint(bare, 5, 5, 0.0)
    assert result.equals(Point(5, 5))


def test_rect_sides_returns_two_perpendicular_unit_vectors():
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    sides = _rect_sides(square)
    assert len(sides) == 2
    (ux1, uz1, len1), (ux2, uz2, len2) = sides
    assert len1 == pytest.approx(10.0)
    assert len2 == pytest.approx(10.0)
    assert ux1 * ux2 + uz1 * uz2 == pytest.approx(0.0, abs=1e-6)  # перпендикулярны


def test_main_directions_returns_four_directions_for_a_rectangle():
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert len(_main_directions(square)) == 4


def test_mst_edges_empty_for_fewer_than_two_points():
    assert _mst_edges([]) == []
    assert _mst_edges([Point(0, 0)]) == []


def test_mst_edges_connects_all_points_with_n_minus_1_edges():
    points = [Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)]
    edges = _mst_edges(points)
    assert len(edges) == 3
    touched = {i for edge in edges for i in edge}
    assert touched == {0, 1, 2, 3}


def test_mst_edges_prefers_shortest_connections():
    # Точка 2 гораздо ближе к точке 1, чем к точке 0 -- MST должен соединить
    # именно так, а не по порядку индексов.
    points = [Point(0, 0), Point(100, 0), Point(101, 0)]
    edges = _mst_edges(points)
    assert (1, 2) in edges or (2, 1) in edges


# --- design_area на реальных участках: разные style/elements ----------------


@pytest.mark.parametrize("style", ["auto", "spine", "grid", "perimeter", "diagonal"])
def test_design_area_every_style_produces_a_valid_scene_without_violations(location_scene, style):
    scene = location_scene[2].model_copy(deep=True)  # двор с тремя зданиями -- есть где развернуться
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "style": style, "elements": list(DEFAULT_ELEMENTS)}]),
        CATALOG,
    )
    full_scene = result.scene
    assert len(full_scene.objects) >= len(scene.objects)
    _assert_no_setback_violations(full_scene)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 6])
def test_design_area_auto_style_on_every_real_location(location_scene, n):
    scene = location_scene[n].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "elements": list(ELEMENTS)}]),  # все элементы, включая фонтан и кусты
        CATALOG,
    )
    _assert_no_setback_violations(result.scene)


def test_design_area_with_fountain_request_may_add_a_fountain_when_there_is_room(location_scene):
    scene = location_scene[2].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "elements": ["paths", "fountain"]}]),
        CATALOG,
    )
    # Не гарантируем, что фонтан обязательно встанет (не в каждом дворе для
    # него есть место, см. докстринг _fit_plaza) -- но применение не должно
    # ни упасть, ни быть безоговорочно отклонено целиком.
    assert result.applied


def test_design_area_on_a_site_with_no_buildings_at_all():
    """_enclosure() возвращает (0.0, 0) сразу, без обхода контура, когда во
    дворе вообще нет зданий (self.buildings пуст) -- сценарий, которого нет
    ни в одной реальной локации (там у каждой есть хотя бы один дом)."""
    from schemas import Boundary, Point2, Scene, SceneMeta

    scene = Scene(
        boundary=Boundary(
            polygon=[Point2(x=-40, z=-40), Point2(x=40, z=-40), Point2(x=40, z=40), Point2(x=-40, z=40)], sourceLayer="X"
        ),
        restrictions=[],
        objects=[],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )
    result = apply_plan(scene, LlmPlan(operations=[{"op": "design_area", "elements": ["paths", "trees"]}]), CATALOG)
    # Без подъездов сеть дорожек не строится (нечего соединять), но деревья
    # разбросом по свободной площади -- должны появиться, вызов не должен упасть.
    assert result.applied or result.rejected  # не падает в любом случае
    assert any(o.type == "tree" for o in result.scene.objects)


def test_design_area_rejects_when_catalog_item_missing():
    from schemas import Boundary, Point2, Scene, SceneMeta

    scene = Scene(
        boundary=Boundary(polygon=[Point2(x=-20, z=-20), Point2(x=20, z=-20), Point2(x=20, z=20), Point2(x=-20, z=20)], sourceLayer="X"),
        restrictions=[],
        objects=[],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )
    result = apply_plan(scene, LlmPlan(operations=[{"op": "design_area"}]), catalog=[])  # пустой каталог
    assert result.rejected
    assert "дизайн двора" in result.rejected[0]


def test_design_area_warns_about_unknown_elements(location_scene):
    scene = location_scene[1].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "elements": ["paths", "swimming_pool"]}]),
        CATALOG,
    )
    assert any("swimming_pool" in w for w in result.warnings)


def test_design_area_unknown_style_warns_and_falls_back_to_auto(location_scene):
    scene = location_scene[1].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "style": "spiral_galaxy"}]),
        CATALOG,
    )
    assert any("spiral_galaxy" in w for w in result.warnings)
    assert result.applied  # всё равно построился (авто-выбор)


def test_design_area_explicit_area_restricts_to_a_circle(location_scene):
    scene = location_scene[2].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "elements": ["trees"], "x": 0.0, "z": 0.0, "radius_m": 10.0}]),
        CATALOG,
    )
    new_trees = [o for o in result.scene.objects if o.type == "tree" and o.metadata.get("source") == "llm"]
    for t in new_trees:
        assert math.hypot(t.position.x, t.position.z) <= 10.0 + 1e-6


def _assert_no_setback_violations(scene):
    from shapely.geometry import Polygon as ShapelyPolygon

    zone_polys = [
        (zone, ShapelyPolygon([(p.x, p.z) for p in zone.polygon]))
        for zone in scene.restrictions
        if zone.severity in ("forbidden", "warning") and len(zone.polygon) >= 3
    ]
    catalog_by_id = {item.id: item for item in CATALOG}
    for obj in scene.objects:
        if obj.metadata.get("source") != "llm":
            continue
        item = catalog_by_id.get(obj.metadata.get("catalogId"))
        kind = item.setback_kind if item else None
        if kind not in ("tree", "bush"):
            continue
        shape = cd._footprint(item, obj.position.x, obj.position.z, math.degrees(obj.rotation)) if item else Point(obj.position.x, obj.position.z)
        for zone, poly in zone_polys:
            if not poly.is_valid:
                continue
            required = setback_for(zone.type, kind, zone.minDistance)
            assert shape.distance(poly) >= required - 1e-6, f"{obj.id} нарушает отступ от {zone.name}"
