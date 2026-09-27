"""Чистка результата конвертации реальных DWG-пачек: дырки зон не выбрасываются
(split_holes), дубли точечных объектов убираются, обрывки слоя "Граница улицы"
и крошечные "здания" не становятся зонами. Каждая проблема найдена на реальных
пачках Мосгеотреста (Харьковская, Олимпийская деревня, Старый Гай)."""

import pytest
from shapely.geometry import Point, Polygon

from dxf_parsing.geometry import split_holes
from dxf_parsing.objects import dedupe_point_objects
from parse_dxf import Transform, extract_restrictions, parse_dxf_doc


def _zone_polys(zones, zone_type=None):
    return [Polygon([(p["x"], p["z"]) for p in z["polygon"]]) for z in zones if zone_type is None or z["type"] == zone_type]


# --- split_holes ---------------------------------------------------------------


def test_split_holes_returns_hole_free_pieces_with_the_same_area():
    ring = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)], [[(40, 40), (60, 40), (60, 60), (40, 60)]])
    pieces = split_holes(ring)
    assert pieces and all(not p.interiors for p in pieces)
    assert sum(p.area for p in pieces) == pytest.approx(ring.area)
    assert not any(p.contains(Point(50, 50)) for p in pieces)


def test_split_holes_handles_several_holes_and_plain_polygons():
    square = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    holes = [[(10, 10), (20, 10), (20, 20), (10, 20)], [(70, 60), (80, 60), (80, 90), (70, 90)]]
    pieces = split_holes(Polygon(square.exterior, holes))
    assert all(not p.interiors for p in pieces)
    assert sum(p.area for p in pieces) == pytest.approx(10000 - 100 - 300)
    assert split_holes(square) == [square]


# --- Кольцевая трасса сети -------------------------------------------------------


def test_ring_pipeline_does_not_forbid_the_block_inside_it(empty_doc):
    # Трасса газопровода замыкается вокруг квартала 100 x 100 м. Раньше охранная
    # зона сохранялась одним внешним контуром, и запрет заливал весь квартал.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], dxfattribs={"layer": "Газопровод"})
    zones = extract_restrictions(msp, Transform())
    polys = _zone_polys(zones, "gas_pipeline")
    assert polys
    assert not any(p.contains(Point(50, 50)) for p in polys)
    assert sum(p.area for p in polys) < 100 * 2 * 2 * 4 * 1.1  # ~ длина трассы x ширина коридора


def test_ground_zone_does_not_cover_buildings_inside_it(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_lwpolyline([(40, 40), (60, 40), (60, 60), (40, 60)], close=True, dxfattribs={"layer": "BUILDING"})
    scene = parse_dxf_doc(empty_doc, center=False)
    ground = [p for p, z in zip(_zone_polys(scene["restrictions"]), scene["restrictions"]) if z["name"] == "__computed_ground__"]
    assert ground
    assert not any(p.contains(Point(50, 50)) for p in ground)
    assert sum(p.area for p in ground) <= 100 * 100 - 20 * 20 + 1


# --- Обрывки -----------------------------------------------------------------------


def test_street_boundary_layer_is_not_a_roadway(empty_doc):
    msp = empty_doc.modelspace()
    for i in range(20):
        msp.add_lwpolyline([(i * 3, 0), (i * 3 + 1, 0), (i * 3 + 1, 1.2), (i * 3, 1.2)], close=True, dxfattribs={"layer": "Граница улицы"})
    assert extract_restrictions(msp, Transform()) == []


def test_tiny_building_fragments_do_not_become_buildings(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1, 0), (1, 1.5), (0, 1.5)], close=True, dxfattribs={"layer": "Части зданий"})
    msp.add_lwpolyline([(10, 10), (22, 10), (22, 20), (10, 20)], close=True, dxfattribs={"layer": "Здания"})
    buildings = [z for z in extract_restrictions(msp, Transform()) if z["type"] == "building"]
    assert len(buildings) == 1
    assert _zone_polys(buildings)[0].area == pytest.approx(120)


# --- Дубли точечных объектов ---------------------------------------------------------


def _obj(obj_type, x, z, **metadata):
    return {"id": f"{obj_type}_{x}_{z}", "type": obj_type, "position": {"x": x, "y": 0.0, "z": z}, "metadata": metadata}


def test_dedupe_keeps_one_object_per_place_preferring_tree_and_named_species():
    objects = [
        _obj("tree", 10.0, 10.0, sourceLayer="! ПР ДЕРЕВЬЯ"),
        _obj("tree", 10.02, 9.98, sourceLayer="! ПР ДЕРЕВЬЯ", species="Липа мелколистная"),
        _obj("tree", 10.0, 10.0, sourceLayer="! ПР ДЕРЕВЬЯ"),
        _obj("bush", 30.0, 5.0, sourceLayer="(ГП) Привязка дер и куст"),
        _obj("tree", 30.0, 5.0, sourceLayer="! ПР ДЕРЕВЬЯ"),
        _obj("lamp", 30.0, 5.0),
        _obj("tree", 12.0, 10.0),
    ]
    kept = dedupe_point_objects(objects)
    assert [(o["type"], o["position"]["x"]) for o in kept] == [("tree", 10.02), ("tree", 30.0), ("lamp", 30.0), ("tree", 12.0)]
    assert kept[0]["metadata"]["species"] == "Липа мелколистная"


def test_parse_merges_the_same_tree_layer_coming_from_several_files(empty_doc):
    # Пачка сливается по слоям: одна и та же проектная посадка из посадочного
    # плана, АПОТ и генплана приходит тремя одинаковыми точками.
    msp = empty_doc.modelspace()
    for _ in range(3):
        msp.add_point((5, 5), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
        msp.add_point((25, 8), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    scene = parse_dxf_doc(empty_doc, center=False)
    assert sum(o["type"] == "tree" for o in scene["objects"]) == 2
