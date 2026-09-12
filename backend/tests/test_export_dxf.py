"""export_dxf.py -- сцена -> DXF, зеркало parser/parse_dxf.py. Проверяем и
отдельные функции записи слоёв, и полный round-trip: экспорт -> ezdxf.read ->
parse_dxf.parse_dxf_doc на настоящих участках (та же методология, что
применялась при разработке фичи -- см. историю сессии)."""

import io
import math

import export_dxf as ed
import ezdxf
import pytest
from courtyard_design import _footprint as courtyard_footprint
from helpers import make_object, make_scene, make_zone
from parse_dxf import parse_dxf_doc
from plant_catalog import CatalogItem, CatalogItemDimensions, CatalogItemRender, catalog_by_id
from schemas import Point2, Point3


def _doc_and_msp():
    doc = ezdxf.new(ed.DXF_VERSION, setup=False)
    if "GREENCITY" not in doc.appids:
        doc.appids.new("GREENCITY")
    return doc, doc.modelspace()


# --- _safe_layer_name / _object_layer / _zone_layer ---------------------------


def test_safe_layer_name_strips_illegal_characters():
    assert ed._safe_layer_name('bad<>/\\":;?*|,=`name', "FALLBACK") == "badname"


def test_safe_layer_name_falls_back_when_nothing_left():
    assert ed._safe_layer_name('<>/\\":;?*|,=`', "FALLBACK") == "FALLBACK"


def test_safe_layer_name_truncates_to_63_chars():
    assert len(ed._safe_layer_name("x" * 100, "FALLBACK")) == 63


def test_object_layer_prefers_source_layer_when_present():
    obj = make_object("t1", "tree", 0, 0, metadata={"sourceLayer": "MY_TREES"})
    assert ed._object_layer(obj) == "MY_TREES"


def test_object_layer_falls_back_to_uppercased_type():
    obj = make_object("b1", "bench", 0, 0)
    assert ed._object_layer(obj) == "BENCH"


def test_object_layer_remaps_colliding_type_names():
    path_obj = make_object("p1", "path_segment", 0, 0)
    lawn_obj = make_object("l1", "lawn_patch", 0, 0)
    assert ed._object_layer(path_obj) == "PAVING"
    assert ed._object_layer(lawn_obj) == "TURF"
    assert "PATH" not in ed._object_layer(path_obj)
    assert "LAWN" not in ed._object_layer(lawn_obj)


def test_zone_layer_prefers_zone_name():
    assert ed._zone_layer("building", "BUILDING_1") == "BUILDING_1"


def test_zone_layer_falls_back_to_type_when_name_empty():
    assert ed._zone_layer("building", "") == "BUILDING"


# --- _footprint ----------------------------------------------------------------


def _hedge_item():
    return CatalogItem(
        id="hedge_segment", category="bush", label="Изгородь", setback_kind="bush", object_type="hedge_segment",
        model="/models/hedge_segment.glb", dimensions=CatalogItemDimensions(height=0.9, width=2.0, depth=0.6),
        render=CatalogItemRender(shape="box", color="#4a7a44"),
    )


def test_footprint_none_without_width_and_depth():
    tree_item = CatalogItem(
        id="tree_medium", category="tree", label="Дерево", setback_kind="tree", object_type="tree",
        model="/models/tree.glb", dimensions=CatalogItemDimensions(height=3.0, radius=0.9),
        render=CatalogItemRender(shape="cone", color="#000"),
    )
    assert ed._footprint(tree_item, 0, 0, 0.0) is None
    assert ed._footprint(None, 0, 0, 0.0) is None


def test_footprint_matches_courtyard_design_rotation_convention():
    """Регрессия на реальный баг из этой сессии: экспортёр когда-то
    использовал другую (зеркальную) формулу поворота, чем планировщик --
    прямоугольные объекты (мощение/изгородь/клумба) выходили повёрнуты не
    так, как стоят в сцене. Обе функции должны давать одни и те же вершины."""
    item = _hedge_item()
    for rotation_deg in (0, 30, 45, 90, 137.5, 200, 315):
        rotation_rad = math.radians(rotation_deg)
        expected_polygon = courtyard_footprint(item, 5.0, -3.0, rotation_deg)
        expected = sorted((round(x, 6), round(z, 6)) for x, z in expected_polygon.exterior.coords[:-1])
        got = sorted((round(x, 6), round(y, 6)) for x, y in ed._footprint(item, 5.0, -3.0, rotation_rad))
        assert got == expected


def test_footprint_axis_aligned_at_zero_rotation():
    item = _hedge_item()
    footprint = ed._footprint(item, 0.0, 0.0, 0.0)
    xs = sorted({round(p[0], 3) for p in footprint})
    ys = sorted({round(p[1], 3) for p in footprint})
    assert xs == [-1.0, 1.0]  # width/2
    assert ys == [-0.3, 0.3]  # depth/2


# --- _write_boundary / _write_zones / _write_buildings / _write_entrances / _write_facade -


def test_write_boundary_skips_when_absent():
    doc, msp = _doc_and_msp()
    ed._write_boundary(doc, msp, make_scene(boundary=None))
    assert len(list(msp)) == 0


def test_write_boundary_writes_closed_polyline_on_source_layer():
    doc, msp = _doc_and_msp()
    scene = make_scene()
    ed._write_boundary(doc, msp, scene)
    entities = list(msp.query("LWPOLYLINE"))
    assert len(entities) == 1
    assert entities[0].dxf.layer == "TERRITORY_BOUNDARY"
    assert entities[0].closed is True


def test_write_zones_skips_degenerate_polygons():
    doc, msp = _doc_and_msp()
    zone = make_zone(polygon=[Point2(x=0, z=0), Point2(x=1, z=0)])
    scene = make_scene(restrictions=[zone])
    ed._write_zones(doc, msp, scene)
    assert len(list(msp)) == 0


def test_write_zones_writes_xdata_with_severity_and_message():
    doc, msp = _doc_and_msp()
    zone = make_zone(severity="forbidden", message="Отступ от здания")
    scene = make_scene(restrictions=[zone])
    ed._write_zones(doc, msp, scene)
    pl = next(iter(msp.query("LWPOLYLINE")))
    xdata = pl.get_xdata("GREENCITY")
    values = [v for code, v in xdata if code == 1000]
    assert "forbidden" in values
    assert "Отступ от здания" in values


def test_write_zones_layer_prefers_zone_name_over_type():
    doc, msp = _doc_and_msp()
    zone = make_zone(type="building", name="MY_BUILDING")
    ed._write_zones(doc, msp, make_scene(restrictions=[zone]))
    assert next(iter(msp.query("LWPOLYLINE"))).dxf.layer == "MY_BUILDING"


def test_write_buildings_skips_objects_without_footprint():
    doc, msp = _doc_and_msp()
    building = make_object("b1", "building", 0, 0, metadata={"footprint": []})
    ed._write_buildings(doc, msp, make_scene(objects=[building]))
    assert len(list(msp)) == 0


def test_write_buildings_writes_footprint_and_label_with_height():
    doc, msp = _doc_and_msp()
    footprint = [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 10, "z": 10}, {"x": 0, "z": 10}]
    building = make_object("b1", "building", 5, 5, metadata={"footprint": footprint, "name": "Дом 1", "height": 27.0})
    ed._write_buildings(doc, msp, make_scene(objects=[building]))
    text = next(iter(msp.query("TEXT")))
    assert text.dxf.text == "Дом 1 h=27.0m"
    poly = next(iter(msp.query("LWPOLYLINE")))
    assert poly.dxf.layer == "BUILDING_FOOTPRINT"  # без sourceLayer -- запасное имя


def test_write_buildings_label_without_height():
    doc, msp = _doc_and_msp()
    footprint = [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 10, "z": 10}]
    building = make_object("b1", "building", 5, 5, metadata={"footprint": footprint, "name": "Дом 1"})
    ed._write_buildings(doc, msp, make_scene(objects=[building]))
    text = next(iter(msp.query("TEXT")))
    assert text.dxf.text == "Дом 1"


def test_write_buildings_uses_id_when_name_missing():
    doc, msp = _doc_and_msp()
    footprint = [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 10, "z": 10}]
    building = make_object("building_007", "building", 5, 5, metadata={"footprint": footprint})
    ed._write_buildings(doc, msp, make_scene(objects=[building]))
    assert next(iter(msp.query("TEXT"))).dxf.text == "building_007"


def test_write_entrances_only_creates_layer_if_any_entrance_exists():
    doc, msp = _doc_and_msp()
    ed._write_entrances(doc, msp, make_scene())
    assert "ENTRANCES" not in doc.layers


def test_write_entrances_writes_one_circle_per_entrance():
    doc, msp = _doc_and_msp()
    entrances = [make_object("e1", "entrance", 1, 1), make_object("e2", "entrance", 2, 2)]
    ed._write_entrances(doc, msp, make_scene(objects=entrances))
    assert len(list(msp.query("CIRCLE"))) == 2
    assert "ENTRANCES" in doc.layers


def test_write_facade_skips_when_no_quads():
    doc, msp = _doc_and_msp()
    ed._write_facade(doc, msp, [], "WINDOWS", 5)
    assert "WINDOWS" not in doc.layers


def test_write_facade_skips_malformed_quads():
    doc, msp = _doc_and_msp()
    bad_quad = [Point3(x=0, y=0, z=0), Point3(x=1, y=0, z=0)]  # не 4 вершины
    ed._write_facade(doc, msp, [bad_quad], "WINDOWS", 5)
    assert len(list(msp.query("3DFACE"))) == 0


def test_write_facade_writes_3dface_with_z_as_height():
    doc, msp = _doc_and_msp()
    quad = [Point3(x=0, y=3, z=0), Point3(x=1, y=3, z=0), Point3(x=1, y=3, z=1), Point3(x=0, y=3, z=1)]
    ed._write_facade(doc, msp, [quad], "WINDOWS", 5)
    face = next(iter(msp.query("3DFACE")))
    assert face.dxf.vtx0.z == 3  # y сцены (высота) -> z DXF


# --- _write_point_object ---------------------------------------------------


def test_write_point_object_plain_tree_gets_point_and_circle_marker():
    doc, msp = _doc_and_msp()
    tree = make_object("t1", "tree", 3, 4)
    ed._write_point_object(doc, msp, tree, catalog_by_id())
    assert len(list(msp.query("POINT"))) == 1
    assert len(list(msp.query("CIRCLE"))) == 1
    assert len(list(msp.query("LWPOLYLINE"))) == 0


def test_write_point_object_uses_catalog_radius_when_available():
    doc, msp = _doc_and_msp()
    catalog = catalog_by_id()
    tree = make_object("t1", "tree", 0, 0, metadata={"catalogId": "tree_tall"})
    ed._write_point_object(doc, msp, tree, catalog)
    circle = next(iter(msp.query("CIRCLE")))
    assert circle.dxf.radius == catalog["tree_tall"].dimensions.radius


def test_write_point_object_caps_marker_radius_at_1_5m():
    doc, msp = _doc_and_msp()
    huge_item = CatalogItem(
        id="huge", category="tree", label="huge", setback_kind="tree", object_type="tree",
        model="/x.glb", dimensions=CatalogItemDimensions(height=10.0, radius=50.0),
        render=CatalogItemRender(shape="cone", color="#000"),
    )
    tree = make_object("t1", "tree", 0, 0, metadata={"catalogId": "huge"})
    ed._write_point_object(doc, msp, tree, {"huge": huge_item})
    circle = next(iter(msp.query("CIRCLE")))
    assert circle.dxf.radius == 1.5


def test_write_point_object_with_footprint_writes_polygon_not_marker():
    doc, msp = _doc_and_msp()
    hedge = make_object("h1", "hedge_segment", 0, 0, metadata={"catalogId": "hedge_segment"})
    ed._write_point_object(doc, msp, hedge, catalog_by_id())
    assert len(list(msp.query("LWPOLYLINE"))) == 1
    assert len(list(msp.query("POINT"))) == 0


def test_write_point_object_color_by_type_falls_back_to_default_green():
    doc, msp = _doc_and_msp()
    tree = make_object("t1", "tree", 0, 0)
    ed._write_point_object(doc, msp, tree, catalog_by_id())
    assert doc.layers.get("TREE").color == ed._ACI_DEFAULT_OBJECT


def test_write_point_object_lamp_uses_its_own_aci_color():
    doc, msp = _doc_and_msp()
    lamp = make_object("l1", "lamp", 0, 0)
    ed._write_point_object(doc, msp, lamp, catalog_by_id())
    assert doc.layers.get("LAMP").color == ed._ACI_BY_OBJECT_TYPE["lamp"]


# --- scene_to_dxf: сборка целиком ------------------------------------------


def test_scene_to_dxf_sets_insunits_to_meters():
    doc = ed.scene_to_dxf(make_scene())
    assert doc.header["$INSUNITS"] == 6


def test_scene_to_dxf_registers_greencity_appid():
    doc = ed.scene_to_dxf(make_scene())
    assert "GREENCITY" in doc.appids


def test_scene_to_dxf_does_not_duplicate_building_or_entrance_as_point_objects():
    footprint = [{"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 10, "z": 10}]
    building = make_object("b1", "building", 5, 5, metadata={"footprint": footprint})
    entrance = make_object("e1", "entrance", 1, 1)
    doc = ed.scene_to_dxf(make_scene(objects=[building, entrance]))
    msp = doc.modelspace()
    # Здание уже как LWPOLYLINE+TEXT, подъезд -- как CIRCLE на ENTRANCES; ни
    # тот, ни другой не должны получить ЕЩЁ и точечный маркер (POINT+CIRCLE
    # общего вида), как обычный объект каталога.
    assert len(list(msp.query("POINT"))) == 0


def test_scene_to_dxf_writes_both_facade_layers():
    quad = [Point3(x=0, y=3, z=0), Point3(x=1, y=3, z=0), Point3(x=1, y=3, z=1), Point3(x=0, y=3, z=1)]
    scene = make_scene()
    scene.windows = [quad]
    scene.canopies = [quad]
    doc = ed.scene_to_dxf(scene)
    assert len(list(doc.modelspace().query("3DFACE"))) == 2


def test_scene_to_dxf_is_a_writable_valid_document():
    doc = ed.scene_to_dxf(make_scene(objects=[make_object("t1", "tree", 1, 1)]))
    buf = io.StringIO()
    doc.write(buf)
    reopened = ezdxf.read(io.StringIO(buf.getvalue()))
    assert len(list(reopened.modelspace())) > 0


# --- Круговая проверка на настоящих участках (round-trip) --------------------


@pytest.mark.parametrize("n", [1, 2, 3, 4, 6])
def test_round_trip_on_real_locations_preserves_boundary_exactly(location_scene, n):
    scene = location_scene[n].model_copy(deep=True)
    doc = ed.scene_to_dxf(scene)
    buf = io.StringIO()
    doc.write(buf)
    reopened = ezdxf.read(io.StringIO(buf.getvalue()))
    reparsed = parse_dxf_doc(reopened, scale=1.0, center=False)

    assert reparsed["boundary"] is not None
    orig = [(p.x, p.z) for p in scene.boundary.polygon]
    back = [(p["x"], p["z"]) for p in reparsed["boundary"]["polygon"]]
    assert len(orig) == len(back)
    max_err = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(orig, back))
    assert max_err < 1e-6


@pytest.mark.parametrize("n", [1, 6])
def test_round_trip_does_not_explode_restriction_zone_count(location_scene, n):
    """Регрессия на реальный баг из этой сессии: слои PATH_SEGMENT/LAWN_PATCH
    содержали подстроки PATH/LAWN, которые parser.POLYGON_RULES распознавал
    как зоны -- сотни плиток дорожки/газона превращались в сотни лишних зон
    при повторном импорте (15 -> 480 на локации 1)."""
    from llm_editor import LlmPlan, apply_plan
    from plant_catalog import load_catalog

    scene = location_scene[n].model_copy(deep=True)
    result = apply_plan(
        scene,
        LlmPlan(operations=[{"op": "design_area", "elements": ["paths", "trees", "hedge", "flowerbeds"]}]),
        load_catalog(),
    )
    full_scene = result.scene

    doc = ed.scene_to_dxf(full_scene)
    buf = io.StringIO()
    doc.write(buf)
    reopened = ezdxf.read(io.StringIO(buf.getvalue()))
    reparsed = parse_dxf_doc(reopened, scale=1.0, center=False)

    assert len(reparsed["restrictions"]) <= 3 * len(full_scene.restrictions) + 5
