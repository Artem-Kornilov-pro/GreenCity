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


# --- Граница участка из слоёв проекта и "след данных" -----------------------------


def _square(msp, x0, y0, size, layer):
    msp.add_lwpolyline([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)], close=True, dxfattribs={"layer": layer})


def _boundary_poly(scene):
    return Polygon([(p["x"], p["z"]) for p in scene["boundary"]["polygon"]])


def test_project_work_boundary_layer_is_the_site_boundary(empty_doc):
    msp = empty_doc.modelspace()
    _square(msp, 1000, 2000, 100, "ДВ_ГП_П_Граница работ")
    scene = parse_dxf_doc(empty_doc)
    assert scene["boundary"]["sourceLayer"] == "ДВ_ГП_П_Граница работ"
    assert _boundary_poly(scene).area == pytest.approx(10000)
    assert scene["meta"]["origin"] == {"x": pytest.approx(1050), "y": pytest.approx(2050)}


def test_work_boundary_drawn_as_separate_lines_is_assembled(empty_doc):
    # Куликовская: граница работ -- 1120 отдельных LINE, ни одного контура.
    msp = empty_doc.modelspace()
    for a, b in (((0, 0), (100, 0)), ((100, 0), (100, 80)), ((100, 80), (0, 80)), ((0, 80), (0, 0))):
        msp.add_line(a, b, dxfattribs={"layer": "ДВ_ГП_П_Граница работ"})
    scene = parse_dxf_doc(empty_doc, center=False)
    assert _boundary_poly(scene).area == pytest.approx(8000, rel=0.01)


def test_far_piece_of_work_boundary_is_dropped(empty_doc):
    # Харьковская: кусок на том же слое за 4,5 км (врезка или соседний лист) --
    # вогнутая оболочка тянула к нему полосу через весь город.
    msp = empty_doc.modelspace()
    _square(msp, 0, 0, 200, "ДВ_ГП_П_Граница работ")
    _square(msp, 4500, 0, 60, "ДВ_ГП_П_Граница работ")
    scene = parse_dxf_doc(empty_doc, center=False)
    assert _boundary_poly(scene).bounds[2] < 300


def test_tiny_piece_on_work_boundary_layer_is_not_trusted(empty_doc):
    # Академика Понтрягина: на слое "Граница работ" генплана -- кусок 322 м².
    msp = empty_doc.modelspace()
    _square(msp, 0, 0, 15, "ДВ_ГП_П_Граница работ")
    for i in range(30):
        msp.add_point((i * 10, 50), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    scene = parse_dxf_doc(empty_doc, center=False)
    assert scene["boundary"]["sourceLayer"] == "__estimated_from_content__"


def test_objects_and_curbs_far_outside_real_boundary_are_dropped(empty_doc):
    msp = empty_doc.modelspace()
    _square(msp, 0, 0, 100, "ДВ_ГП_П_Граница работ")
    msp.add_point((50, 50), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    msp.add_point((10000, 50), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    msp.add_lwpolyline([(20, 20), (80, 20)], dxfattribs={"layer": "Бортовой камень"})
    msp.add_lwpolyline([(9000, 20), (9080, 20)], dxfattribs={"layer": "Бортовой камень"})
    scene = parse_dxf_doc(empty_doc, center=False)
    assert [o["position"]["x"] for o in scene["objects"] if o["type"] == "tree"] == [50]
    assert len(scene["curbs"]) == 1


def test_estimated_boundary_follows_data_not_a_hull_over_empty_land(empty_doc):
    # Академика Понтрягина: оболочка всех точек накрывала пустоту между
    # участками и тянулась к одинокому далёкому объекту -- 195 га "участка".
    msp = empty_doc.modelspace()
    for i in range(40):  # улица 400 x 20 м вдоль X
        msp.add_point((i * 10, 0), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
        msp.add_point((i * 10, 20), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    for i in range(30):  # поперечная улица 20 x 300 м вдоль Z
        msp.add_point((0, i * 10), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
        msp.add_point((20, i * 10), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    scene = parse_dxf_doc(empty_doc, center=False)
    boundary = _boundary_poly(scene)
    assert boundary.covers(Point(200, 10)) and boundary.covers(Point(10, 250))
    assert not boundary.covers(Point(250, 150))  # угол между улицами -- не участок
    assert boundary.area < 400 * 60 + 300 * 60


def test_scene_with_estimated_boundary_is_centered_near_origin(empty_doc):
    # Без слоя границы сцена оставалась в координатах чертежа -- у реальных
    # DWG-пачек 10-15 км от начала, где float32 видеокарты даёт миллиметры
    # точности, и тонкие линии (контуры зон, бордюры, сетка) мерцали.
    msp = empty_doc.modelspace()
    ox, oy = 15000.0, -9000.0
    _square(msp, ox + 40, oy + 40, 20, "Здания")
    for i in range(30):
        msp.add_point((ox + i * 5, oy), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
        msp.add_point((ox + i * 5, oy + 100), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    msp.add_lwpolyline([(ox, oy + 50), (ox + 150, oy + 50)], dxfattribs={"layer": "Бортовой камень"})
    scene = parse_dxf_doc(empty_doc)
    assert scene["boundary"]["sourceLayer"] == "__estimated_from_content__"
    xs = [o["position"]["x"] for o in scene["objects"]] + [p["x"] for c in scene["curbs"] for p in c]
    zs = [o["position"]["z"] for o in scene["objects"]] + [p["z"] for c in scene["curbs"] for p in c]
    assert max(map(abs, xs)) < 200 and max(map(abs, zs)) < 200
    # По meta.origin восстанавливаются исходные координаты чертежа.
    origin = scene["meta"]["origin"]
    tree = next(o for o in scene["objects"] if o["type"] == "tree")
    assert tree["position"]["x"] + origin["x"] == pytest.approx(ox, abs=0.01)
    assert tree["position"]["z"] + origin["y"] == pytest.approx(oy, abs=0.01)
    # Контур здания и его footprint сдвинуты одинаково (ровно один раз).
    building = next(o for o in scene["objects"] if o["type"] == "building")
    zone = next(z for z in scene["restrictions"] if z["type"] == "building")
    assert building["metadata"]["footprint"] == zone["polygon"]
    assert min(p["x"] for p in zone["polygon"]) + origin["x"] == pytest.approx(ox + 40, abs=0.01)


# --- Дубли бордюров и зданий (z-fighting: мерцание при движении камеры) ------------


def _pl(*pts):
    return [{"x": float(x), "z": float(z)} for x, z in pts]


def _curb_length(polylines):
    return sum(((b["x"] - a["x"]) ** 2 + (b["z"] - a["z"]) ** 2) ** 0.5 for pl in polylines for a, b in zip(pl, pl[1:]))


def test_dedupe_curbs_drops_repeated_curb_from_another_file():
    from dxf_parsing.objects import dedupe_curbs

    survey = _pl((0, 0), (50, 0), (50, 30))
    design = _pl((0, 0.02), (50, 0.02))  # тот же борт из проекта, в 2 см
    other_edge = _pl((0, 0.15), (50, 0.15))  # вторая кромка камня -- не дубль
    kept = dedupe_curbs([survey, design, other_edge])
    assert _curb_length(kept) == pytest.approx(50 + 30 + 50)
    assert kept[0] == survey


def test_dedupe_curbs_keeps_the_new_part_of_a_partly_repeated_curb():
    from dxf_parsing.objects import dedupe_curbs

    first = _pl((0, 0), (20, 0))
    longer = _pl((0, 0), (20, 0), (40, 0))  # первая половина -- дубль, вторая -- новая
    kept = dedupe_curbs([first, longer])
    assert _curb_length(kept) == pytest.approx(40)


def test_parse_keeps_one_copy_of_a_building_coming_from_several_files(empty_doc):
    msp = empty_doc.modelspace()
    _square(msp, 0, 0, 20, "Здания")
    _square(msp, 0.01, 0.01, 20, "Здания")  # тот же дом из второго файла пачки
    _square(msp, 50, 0, 20, "Здания")
    buildings = [z for z in extract_restrictions(msp, Transform()) if z["type"] == "building"]
    assert len(buildings) == 2
