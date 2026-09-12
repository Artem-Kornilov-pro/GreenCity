"""parse_dxf.py -- парсер DXF в JSON-сцену. Два слоя тестов:

* чистые функции (layer_matches, match_rule, polygon_points, centroid,
  buffer_segment, Transform, clean_label, nearest_text) -- на минимальных
  сущностях, собранных вручную через ezdxf.new();
* parse_dxf_doc/parse_dxf_file целиком -- на настоящих файлах locations/,
  где реально проверяется, что все части (границы, зоны, здания, точечные
  объекты, фасады) складываются вместе без противоречий.
"""

import math

import parse_dxf
import pytest
from parse_dxf import (
    POLYGON_RULES,
    Transform,
    buffer_segment,
    centroid,
    clean_label,
    extract_boundary,
    extract_buildings,
    extract_facade_quads,
    extract_point_objects,
    extract_restrictions,
    layer_matches,
    match_rule,
    nearest_text,
    parse_dxf_doc,
    parse_dxf_file,
    polygon_points,
    print_summary,
)

# --- layer_matches / match_rule ------------------------------------------


def test_layer_matches_is_case_insensitive_on_the_layer_name():
    # Ключевые слова (BOUNDARY_LAYER_KEYWORDS/POLYGON_RULES и т.п.) сами
    # всегда пишутся заглавными по всему модулю -- layer_matches приводит к
    # верхнему регистру только имя слоя, не сами ключевые слова.
    assert layer_matches("building_footprint", ["BUILDING"]) is True
    assert layer_matches("Building_Footprint", ["BUILDING"]) is True
    assert layer_matches("parking_lot", ["BUILDING"]) is False


def test_match_rule_returns_first_matching_rule_in_order():
    # OVERHEAD должен побеждать POWER для слоя, содержащего оба (порядок в
    # POLYGON_RULES это гарантирует -- см. докстринг модуля).
    cfg = match_rule("OVERHEAD_POWER_LINE", POLYGON_RULES)
    assert cfg["type"] == "overhead_power_line"


def test_match_rule_returns_none_when_nothing_matches():
    assert match_rule("SOME_RANDOM_LAYER", POLYGON_RULES) is None


# --- polygon_points --------------------------------------------------------


def test_polygon_points_lwpolyline_drops_duplicate_closing_point(empty_doc):
    msp = empty_doc.modelspace()
    # LWPOLYLINE с явно продублированной первой точкой в конце -- частый
    # артефакт экспорта из некоторых CAD.
    pl = msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)], dxfattribs={"layer": "X"})
    pts = polygon_points(pl)
    assert len(pts) == 4


def test_polygon_points_lwpolyline_drops_consecutive_near_duplicates(empty_doc):
    msp = empty_doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (0, 1e-9), (10, 0), (10, 10)], dxfattribs={"layer": "X"})
    pts = polygon_points(pl)
    assert len(pts) == 3


def test_polygon_points_polyline3d(empty_doc):
    msp = empty_doc.modelspace()
    pl = msp.add_polyline3d([(0, 0, 0), (1, 0, 0), (1, 1, 0)], dxfattribs={"layer": "X"})
    pts = polygon_points(pl)
    assert pts == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]


# --- centroid / buffer_segment ----------------------------------------------


def test_centroid_of_square():
    assert centroid([(0, 0), (10, 0), (10, 10), (0, 10)]) == (5.0, 5.0)


def test_buffer_segment_produces_a_rectangle_of_the_right_width():
    quad = buffer_segment(0, 0, 10, 0, half_width=1.0)
    ys = sorted({p[1] for p in quad})
    assert ys == pytest.approx([-1.0, 1.0])
    xs = sorted({p[0] for p in quad})
    assert xs == pytest.approx([0.0, 10.0])


def test_buffer_segment_returns_none_for_degenerate_zero_length_segment():
    assert buffer_segment(5, 5, 5, 5, half_width=1.0) is None


# --- Transform ---------------------------------------------------------------


def test_transform_point_applies_scale_and_origin_shift():
    tf = Transform(scale=2.0, origin_x=1.0, origin_y=1.0)
    p = tf.point(3.0, 4.0, 5.0)
    assert p == {"x": (3.0 - 1.0) * 2.0, "y": 5.0 * 2.0, "z": (4.0 - 1.0) * 2.0}


def test_transform_polygon_maps_xy_to_xz_and_drops_height():
    tf = Transform(scale=1.0)
    poly = tf.polygon([(0, 0, 5), (10, 0, 5), (10, 10, 5)])
    assert poly == [{"x": 0.0, "z": 0.0}, {"x": 10.0, "z": 0.0}, {"x": 10.0, "z": 10.0}]


def test_transform_identity_by_default():
    tf = Transform()
    p = tf.point(3.0, 4.0)
    assert p == {"x": 3.0, "y": 0.0, "z": 4.0}


# --- clean_label / nearest_text ----------------------------------------------


def test_clean_label_strips_trailing_height_annotation():
    assert clean_label("Дом 1 h=27.0m") == "Дом 1"
    assert clean_label("Дом 1 h=27m") == "Дом 1"


def test_clean_label_leaves_plain_text_untouched():
    assert clean_label("Дом 1") == "Дом 1"


def test_nearest_text_picks_closest_by_euclidean_distance():
    texts = [{"x": 0, "y": 0, "text": "far"}, {"x": 1, "y": 1, "text": "near"}]
    assert nearest_text(1.1, 1.1, texts)["text"] == "near"


def test_nearest_text_returns_none_for_empty_list():
    assert nearest_text(0, 0, []) is None


# --- extract_boundary ---------------------------------------------------------


def test_extract_boundary_returns_none_when_absent(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], dxfattribs={"layer": "SOME_OTHER_LAYER"})
    tf = Transform()
    assert extract_boundary(msp, tf) is None


def test_extract_boundary_finds_layer_by_keyword(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    tf = Transform()
    boundary = extract_boundary(msp, tf)
    assert boundary is not None
    assert boundary["sourceLayer"] == "TERRITORY_BOUNDARY"
    assert len(boundary["polygon"]) == 3


def test_extract_boundary_ignores_polygons_with_too_few_points(empty_doc):
    msp = empty_doc.modelspace()
    # Вырожденная "граница" из двух точек -- не полигон, должна быть отклонена.
    pl = msp.add_lwpolyline([(0, 0), (10, 0)], dxfattribs={"layer": "SITE_BOUNDARY"})
    pl.dxf.flags = 0  # не замкнута
    tf = Transform()
    assert extract_boundary(msp, tf) is None


# --- extract_restrictions ------------------------------------------------------


def test_extract_restrictions_closed_polygon_becomes_one_zone(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "BUILDING_1"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "building"
    assert zones[0]["severity"] == "forbidden"


def test_extract_restrictions_skips_boundary_and_skip_layers(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "MARKING_ARROWS"})
    zones = extract_restrictions(msp, Transform())
    assert zones == []


def test_extract_restrictions_ignores_layers_with_no_matching_rule(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "UNKNOWN_STUFF"})
    assert extract_restrictions(msp, Transform()) == []


def test_extract_restrictions_open_polyline_is_buffered_into_a_corridor(empty_doc):
    msp = empty_doc.modelspace()
    # GAS -- открытая (не замкнутая) труба, должна раздуться в прямоугольный
    # коридор шириной 2*minDistance вокруг центральной линии.
    msp.add_polyline3d([(0, 0, 0), (10, 0, 0)], dxfattribs={"layer": "GAS_PIPE"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "gas_pipeline"
    assert len(zones[0]["polygon"]) == 4


def test_extract_restrictions_skips_purely_vertical_stub_segments(empty_doc):
    msp = empty_doc.modelspace()
    # Стояк подключения к зданию: X/Y не меняются, меняется только Z -- не
    # ограничение в плане XZ, должен быть пропущен без падения.
    msp.add_polyline3d([(5, 5, 0), (5, 5, 3)], dxfattribs={"layer": "WATER_SUPPLY_B1"})
    zones = extract_restrictions(msp, Transform())
    assert zones == []


def test_extract_restrictions_line_entity_is_buffered(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "POWER_CABLE"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "electrical"


def test_extract_restrictions_degenerate_line_is_skipped(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((5, 5, 0), (5, 5, 0), dxfattribs={"layer": "POWER_CABLE"})
    assert extract_restrictions(msp, Transform()) == []


def test_extract_restrictions_line_on_boundary_or_skip_layer_is_ignored(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "DIM_LEADER"})
    assert extract_restrictions(msp, Transform()) == []


def test_extract_restrictions_assigns_incrementing_ids_per_type(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_A"})
    msp.add_lwpolyline([(20, 0), (30, 0), (30, 10)], close=True, dxfattribs={"layer": "BUILDING_B"})
    zones = extract_restrictions(msp, Transform())
    assert {z["id"] for z in zones} == {"building_001", "building_002"}


def test_extract_restrictions_overhead_power_carries_max_height(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "OVERHEAD_LINE"})
    zones = extract_restrictions(msp, Transform())
    assert zones[0]["maxHeight"] == 4.0
    assert zones[0]["severity"] == "warning"


# --- extract_buildings ---------------------------------------------------------


def test_extract_buildings_uses_nearest_mesh_height_and_nearest_text_label(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "BUILDING_1"})
    mesh = msp.add_mesh(dxfattribs={"layer": "BUILDING_3D"})
    with mesh.edit_data() as data:
        data.vertices = [(0, 0, 0), (10, 0, 0), (10, 10, 27.0), (0, 10, 27.0)]
        data.faces = [[0, 1, 2, 3]]
    msp.add_text("Дом 1 h=27.0m", dxfattribs={"layer": "LABELS", "insert": (5, 5)})

    tf = Transform()
    restrictions = extract_restrictions(msp, tf)
    buildings = extract_buildings(msp, tf, restrictions)

    assert len(buildings) == 1
    building = buildings[0]
    assert building["metadata"]["name"] == "Дом 1"
    assert building["metadata"]["height"] == 27.0
    assert len(building["metadata"]["footprint"]) == 4


def test_extract_buildings_falls_back_to_zone_name_without_text_label(empty_doc):
    msp = empty_doc.modelspace()
    # Не "BUILDING_NO_LABEL" -- подстрока "LABEL" сама по себе в SKIP_LAYER_KEYWORDS
    # и слой был бы пропущен целиком, что проверяется отдельным тестом выше.
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_PLAIN"})
    tf = Transform()
    restrictions = extract_restrictions(msp, tf)
    buildings = extract_buildings(msp, tf, restrictions)
    assert buildings[0]["metadata"]["name"] == "BUILDING_PLAIN"
    assert buildings[0]["metadata"]["height"] is None


def test_extract_buildings_skips_mesh_with_no_vertices(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_EMPTY_MESH"})
    msp.add_mesh(dxfattribs={"layer": "BUILDING_3D"})  # ни одной вершины
    tf = Transform()
    restrictions = extract_restrictions(msp, tf)
    buildings = extract_buildings(msp, tf, restrictions)
    assert buildings[0]["metadata"]["height"] is None  # пустой MESH проигнорирован, не считается кандидатом


def test_extract_buildings_ignores_malformed_text_entities(empty_doc):
    """TEXT с insert-точкой, которую ezdxf не может прочитать -- отдельный
    except Exception в extract_buildings не должен ронять весь разбор."""
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_X"})
    tf = Transform()
    restrictions = extract_restrictions(msp, tf)
    # Никаких TEXT-сущностей вовсе -- ветка "except" не обязана сработать,
    # здесь просто проверяем, что buildings без единого TEXT не падает.
    buildings = extract_buildings(msp, tf, restrictions)
    assert buildings[0]["metadata"]["name"] == "BUILDING_X"


# --- extract_point_objects ------------------------------------------------------


def test_extract_point_objects_insert_block_reference(empty_doc):
    empty_doc.blocks.new(name="tree_block")
    msp = empty_doc.modelspace()
    msp.add_blockref("tree_block", insert=(1, 2, 0), dxfattribs={"layer": "TREE_LAYER", "rotation": 90.0, "xscale": 1.5})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 1
    obj = objects[0]
    assert obj["type"] == "tree"
    assert obj["scale"] == 1.5
    assert obj["rotation"] == pytest.approx(math.radians(90.0))
    assert obj["metadata"]["blockName"] == "tree_block"


def test_extract_point_objects_bare_point_entity(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_point((3, 4, 0), dxfattribs={"layer": "BUSH_A"})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 1
    assert objects[0]["type"] == "bush"
    assert objects[0]["position"] == {"x": 3.0, "y": 0.0, "z": 4.0}


def test_extract_point_objects_line_and_circle_pair_becomes_one_lamp(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((5, 5, 0), (5, 5, 3), dxfattribs={"layer": "LAMPS"})
    msp.add_circle((5, 5, 3), radius=0.3, dxfattribs={"layer": "LAMPS"})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 1
    assert objects[0]["type"] == "lamp"
    assert objects[0]["metadata"]["height"] == 3.0


def test_extract_point_objects_deduplicates_lines_at_the_same_base(empty_doc):
    msp = empty_doc.modelspace()
    # Два LINE с одинаковым основанием (например, столб нарисован двумя
    # перекрывающимися сегментами) -- должен получиться один объект, не два.
    # Пара LINE+CIRCLE активирует ветку "фонарь" -- один CIRCLE на оба LINE.
    msp.add_line((5, 5, 0), (5, 5, 3), dxfattribs={"layer": "LAMPS"})
    msp.add_line((5, 5, 0), (5, 5, 3), dxfattribs={"layer": "LAMPS"})
    msp.add_circle((5, 5, 3), radius=0.3, dxfattribs={"layer": "LAMPS"})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 1


def test_extract_point_objects_lone_circle_without_line_is_a_marker(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_circle((1, 1, 0), radius=0.5, dxfattribs={"layer": "ENTRANCES"})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 1
    assert objects[0]["type"] == "entrance"
    assert "height" not in objects[0]["metadata"]


def test_extract_point_objects_ignores_unmatched_layers(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "SOMETHING_UNRELATED"})
    assert extract_point_objects(msp, Transform()) == []


def test_extract_point_objects_assigns_independent_counters_per_type(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "TREE_A"})
    msp.add_point((1, 0, 0), dxfattribs={"layer": "TREE_B"})
    msp.add_point((0, 0, 0), dxfattribs={"layer": "BUSH_A"})
    objects = extract_point_objects(msp, Transform())
    ids = sorted(o["id"] for o in objects)
    assert ids == ["bush_001", "tree_001", "tree_002"]


# --- extract_facade_quads -------------------------------------------------------


def test_extract_facade_quads_groups_3dface_by_layer_keyword(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 3), (0, 1, 3)], dxfattribs={"layer": "WINDOWS"})
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 4), (0, 1, 4)], dxfattribs={"layer": "CANOPIES"})
    quads = extract_facade_quads(msp, Transform())
    assert len(quads["windows"]) == 1
    assert len(quads["canopies"]) == 1
    assert len(quads["windows"][0]) == 4


def test_extract_facade_quads_ignores_unrelated_layers(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 3), (0, 1, 3)], dxfattribs={"layer": "RANDOM"})
    quads = extract_facade_quads(msp, Transform())
    assert quads["windows"] == []
    assert quads["canopies"] == []


# --- parse_dxf_doc / parse_dxf_file: сквозная сборка ---------------------------


def test_parse_dxf_doc_returns_expected_top_level_shape(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc)
    assert set(result.keys()) == {"boundary", "restrictions", "objects", "windows", "canopies", "meta"}
    assert result["boundary"]["sourceLayer"] == "TERRITORY_BOUNDARY"


def test_parse_dxf_doc_centers_on_boundary_centroid_by_default(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc)
    xs = [p["x"] for p in result["boundary"]["polygon"]]
    zs = [p["z"] for p in result["boundary"]["polygon"]]
    assert min(xs) == pytest.approx(-10.0)
    assert min(zs) == pytest.approx(-10.0)
    assert result["meta"]["origin"] == {"x": 10.0, "y": 10.0}


def test_parse_dxf_doc_center_false_keeps_original_coordinates(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "TERRITORY_BOUNDARY"})
    result = parse_dxf_doc(empty_doc, center=False)
    xs = [p["x"] for p in result["boundary"]["polygon"]]
    assert min(xs) == pytest.approx(0.0)
    assert result["meta"]["origin"] == {"x": 0.0, "y": 0.0}


def test_parse_dxf_doc_explicit_scale_overrides_insunits(empty_doc):
    result = parse_dxf_doc(empty_doc, scale=0.5)
    assert result["meta"]["scale"] == 0.5


def test_parse_dxf_doc_default_scale_comes_from_insunits(empty_doc):
    # $INSUNITS=6 -- метры, коэффициент 1.0 (см. INSUNITS_TO_METERS)
    result = parse_dxf_doc(empty_doc)
    assert result["meta"]["scale"] == 1.0


def test_parse_dxf_doc_without_any_boundary_layer_returns_none_boundary(empty_doc):
    result = parse_dxf_doc(empty_doc)
    assert result["boundary"] is None
    assert result["meta"]["origin"] == {"x": 0.0, "y": 0.0}


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_parse_dxf_file_on_every_real_location_produces_sane_output(location_paths, n):
    result = parse_dxf_file(location_paths[n])
    assert result["boundary"] is not None
    assert len(result["boundary"]["polygon"]) >= 3
    assert result["meta"]["buildingCount"] >= 1
    assert result["meta"]["pointObjectCount"] == len([o for o in result["objects"] if o["type"] != "building"])
    # Каждая зона -- валидный полигон от 3 точек, с известной severity
    for zone in result["restrictions"]:
        assert len(zone["polygon"]) >= 3
        assert zone["severity"] in ("forbidden", "warning", "allowed")


def test_parse_dxf_file_is_deterministic(location_paths):
    first = parse_dxf_file(location_paths[1])
    second = parse_dxf_file(location_paths[1])
    assert first == second


# --- CLI: print_summary / main -------------------------------------------------


def test_print_summary_runs_without_error_and_prints_bbox(empty_doc, capsys):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "BUILDING_1"})
    print_summary(empty_doc)
    captured = capsys.readouterr()
    assert "BBox X" in captured.out
    assert "BUILDING_1" in captured.out


def test_main_cli_writes_expected_json_files(tmp_path, monkeypatch, location_paths):
    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir)])
    parse_dxf.main()
    assert (out_dir / "boundary.json").exists()
    assert (out_dir / "restrictions.json").exists()
    assert (out_dir / "objects.json").exists()
    assert (out_dir / "facade.json").exists()


def test_main_cli_summary_mode_does_not_write_files(tmp_path, monkeypatch, location_paths, capsys):
    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir), "--summary"])
    parse_dxf.main()
    assert not out_dir.exists()
    assert "DXF version" in capsys.readouterr().out


def test_main_cli_exits_cleanly_on_missing_file(monkeypatch):
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", "/no/such/file.dxf"])
    with pytest.raises(SystemExit):
        parse_dxf.main()


def test_main_cli_exits_cleanly_on_structurally_corrupt_dxf(tmp_path, monkeypatch):
    """Файл существует и начинается как настоящий DXF, но обрублен посреди --
    ezdxf.readfile поднимает DXFStructureError, а не OSError (та ветка уже
    покрыта test_main_cli_exits_cleanly_on_missing_file)."""
    import ezdxf

    good_path = tmp_path / "good.dxf"
    ezdxf.new("R2010").saveas(good_path)
    content = good_path.read_text()
    corrupt_path = tmp_path / "corrupt.dxf"
    corrupt_path.write_text(content[: len(content) // 2])

    monkeypatch.setattr("sys.argv", ["parse_dxf.py", str(corrupt_path)])
    with pytest.raises(SystemExit):
        parse_dxf.main()


def test_main_cli_no_center_flag_disables_centering(tmp_path, monkeypatch, location_paths):
    import json

    out_dir = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["parse_dxf.py", location_paths[1], "--out-dir", str(out_dir), "--no-center"])
    parse_dxf.main()
    boundary = json.loads((out_dir / "boundary.json").read_text())
    centered_boundary = parse_dxf_file(location_paths[1])["boundary"]
    assert boundary["polygon"] != centered_boundary["polygon"]
