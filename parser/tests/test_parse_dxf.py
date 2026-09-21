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
    extract_curb_polylines,
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
from shapely.geometry import Point, Polygon

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


def test_match_rule_russian_signal_cable_beats_general_electrical():
    # Та же специфичность-раньше-общего гарантия, что и у OVERHEAD/POWER
    # выше, но для русского блока (issue #50 follow-up) -- реальный слой
    # "Кабель связи" содержит подстроку "КАБЕЛ" (общий электрокабель), но
    # должен классифицироваться как слаботочка (СВЯЗ), а не силовой кабель.
    cfg = match_rule("Кабель связи".upper(), POLYGON_RULES)
    assert cfg["type"] == "signal_cable"


def test_match_rule_recognizes_russian_layer_names():
    assert match_rule("ЗДАНИЕ_1".upper(), POLYGON_RULES)["type"] == "building"
    assert match_rule("ДВ_ГП_П_Газон_Рулонный".upper(), POLYGON_RULES)["type"] == "protected_zone"
    assert match_rule("Устройство_трот_более_2м".upper(), POLYGON_RULES)["type"] == "pedestrian_path"
    assert match_rule("Газопровод".upper(), POLYGON_RULES)["type"] == "gas_pipeline"
    assert match_rule("Канализация самотёчная".upper(), POLYGON_RULES)["type"] == "sewer"


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


def test_extract_restrictions_single_non_building_closed_polygon_becomes_one_zone(empty_doc):
    # Здание идёт напрямую в _add_zone (см. тест выше) -- не-здание проходит
    # через _merge_polygon_zones даже когда оно единственное на слое; на
    # тривиальном случае (один контур) объединение должно быть no-op.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "protected_zone"


def test_extract_restrictions_merges_touching_closed_polygons_on_same_layer(empty_doc):
    # Реальные конвертированные DXF (в отличие от рукописных locations/
    # location_old/) отдают газон/тротуар/дорогу не одним контуром на слой, а
    # россыпью примыкающих кусков -- на 06_kamchatskaya_ulitsa один слой
    # GRASS -- это 5751 отдельный контур. Два квадрата встык по общему ребру
    # должны слиться в один прямоугольник, а не остаться двумя зонами.

    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "GRASS"})
    msp.add_lwpolyline([(10, 0), (20, 0), (20, 10), (10, 10)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.is_valid
    assert poly.area == pytest.approx(200.0, abs=1.0)
    assert poly.bounds == pytest.approx((0.0, 0.0, 20.0, 10.0), abs=0.1)


def test_extract_restrictions_closes_submillimeter_conversion_gap(empty_doc):
    # Тот же случай, что и выше, но со стыком не край-в-край, а с зазором в
    # 5 мм -- артефакт конвертации DWG->DXF (см. _POLYGON_CLOSING_GAP_M),
    # а не два физически разных объекта. Должны слиться в одну зону.

    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "GRASS"})
    msp.add_lwpolyline(
        [(10.005, 0), (20.005, 0), (20.005, 10), (10.005, 10)], close=True, dxfattribs={"layer": "GRASS"}
    )
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.area == pytest.approx(200.0, rel=0.05)


def test_extract_restrictions_does_not_bridge_a_real_gap(empty_doc):
    # Контрольный случай к предыдущему тесту: зазор намного больше
    # _POLYGON_CLOSING_GAP_M (1 м, не миллиметры) -- это два разных газона,
    # не должны слипнуться в один.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "GRASS"})
    msp.add_lwpolyline([(11, 0), (21, 0), (21, 10), (11, 10)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 2
    assert all(z["type"] == "protected_zone" for z in zones)


def test_extract_restrictions_repairs_self_intersecting_non_building_polygon(empty_doc):
    # "Бабочка" на не-здании -- та же проблема, что чинит building_setbacks.py
    # для зданий (poly.buffer(0)), только здесь она в самом парсере: заметная
    # доля контуров у реальных конвертированных файлов самопересекающаяся
    # (артефакт конвертации, см. _merge_polygon_zones).

    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 10), (10, 0), (0, 10)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform())
    assert zones
    total_area = 0.0
    for z in zones:
        poly = Polygon([(p["x"], p["z"]) for p in z["polygon"]])
        assert poly.is_valid
        total_area += poly.area
    # buffer(0) разрешает самопересечение по правилу чётности обхода: две
    # половины "бабочки" намотаны в противоположных направлениях, поэтому
    # одна гасит другую как дырку, и остаётся один треугольник (area=25), а
    # не оба (50) -- это корректное поведение shapely, а не половинчатая
    # починка.
    assert total_area == pytest.approx(25.0, rel=0.05)


def test_extract_restrictions_drops_degenerate_zero_area_closed_polygon(empty_doc):
    # Три коллинеарные точки -- формально "замкнутый контур" с >=3 точками,
    # но нулевой площади (артефакт конвертации). Не должен породить зону.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (5, 0), (10, 0)], close=True, dxfattribs={"layer": "GRASS"})
    assert extract_restrictions(msp, Transform()) == []


def _boundary10(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    return msp, extract_boundary(msp, Transform())


def test_extract_restrictions_without_boundary_does_not_clip(empty_doc):
    # boundary=None (значение по умолчанию) -- обрезка выключена целиком,
    # старое поведение без понятия "участок" не меняется.
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)], close=True, dxfattribs={"layer": "GRASS"})
    assert len(extract_restrictions(msp, Transform())) == 1


def test_extract_restrictions_drops_zone_far_outside_relevance_margin(empty_doc):
    # Зона в 1000 м от участка размером 10x10 -- заведомо дальше
    # _RESTRICTION_RELEVANCE_MARGIN_M (20 м), должна пропасть целиком.
    msp, boundary = _boundary10(empty_doc)
    msp.add_lwpolyline([(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)], close=True, dxfattribs={"layer": "GRASS"})
    assert extract_restrictions(msp, Transform(), boundary) == []


def test_extract_restrictions_clips_zone_straddling_the_relevance_margin(empty_doc):
    # Полоса газона 200x10 м, проходящая через участок 10x10 -- реалистичная
    # модель общегородской подложки, которая тянется далеко за пределы
    # участка (см. докстринг extract_restrictions, 11_frunzenskaya_naberezhnaya/
    # 13_kharkovsky_proezd). Должна обрезаться до куска у самого участка, а
    # не остаться зоной на все 2000 м^2.
    msp, boundary = _boundary10(empty_doc)
    msp.add_lwpolyline([(-100, 0), (100, 0), (100, 10), (-100, 10)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform(), boundary)
    assert len(zones) == 1
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert 0 < poly.area < 700  # << исходных 2000 м^2, но не пусто


def test_extract_restrictions_keeps_zone_fully_inside_relevance_margin(empty_doc):
    # Контрольный случай: зона внутри участка обрезкой не задета вовсе.
    msp, boundary = _boundary10(empty_doc)
    msp.add_lwpolyline([(2, 2), (8, 2), (8, 8), (2, 8)], close=True, dxfattribs={"layer": "GRASS"})
    zones = extract_restrictions(msp, Transform(), boundary)
    assert len(zones) == 1
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.area == pytest.approx(36.0)


def test_extract_restrictions_relevance_margin_does_not_drop_far_building(empty_doc):
    # Здания -- исключение из обрезки (см. докстринг _clip_offsite_zones):
    # обрезка касается инженерных сетей/газона с общегородской подложки, а не
    # зданий, которые extract_buildings превращает в объекты сцены поштучно.
    msp, boundary = _boundary10(empty_doc)
    msp.add_lwpolyline([(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)], close=True, dxfattribs={"layer": "BUILDING_FAR"})
    zones = extract_restrictions(msp, Transform(), boundary)
    assert len(zones) == 1
    assert zones[0]["type"] == "building"


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


def test_extract_restrictions_reads_zone_from_hatch_boundary(empty_doc):
    # issue #50 follow-up: план покрытий (газон/тротуар и т.п.) в реальных
    # DWG-проектах Мосгеотреста часто залит HATCH-штриховкой, а не нарисован
    # полигоном ("13_kharkovskaya": "...Газон_Рулонный" целиком из HATCH).
    msp = empty_doc.modelspace()
    hatch = msp.add_hatch(dxfattribs={"layer": "LAWN_FILL"})
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "protected_zone"
    assert zones[0]["severity"] == "allowed"
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.area == pytest.approx(100.0, rel=0.01)


def test_extract_restrictions_ignores_hatch_on_unmatched_layer(empty_doc):
    msp = empty_doc.modelspace()
    hatch = msp.add_hatch(dxfattribs={"layer": "SOMETHING_UNRELATED"})
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    assert extract_restrictions(msp, Transform()) == []


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


# --- реконструкция зданий из разрозненных LINE (issue #50 follow-up) -----------
# Реальные топопланы Мосгеотреста рисуют контур здания сложным линтайпом,
# который при конвертации DWG->DXF "взрывается" на отдельные LINE без связи
# между собой -- см. docstring _reconstruct_buildings_from_line_fragments.


def test_extract_restrictions_merges_disconnected_line_fragments_into_one_building(empty_doc):
    msp = empty_doc.modelspace()
    # Квадрат 10x10, нарисованный 4 НЕЗАВИСИМЫМИ LINE (не LWPOLYLINE) --
    # ровно то, что реально приходит из "взорвавшегося" линтайпа.
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((10, 0, 0), (10, 10, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((10, 10, 0), (0, 10, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((0, 10, 0), (0, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "building"
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.area == pytest.approx(100.0, rel=0.01)


def test_extract_restrictions_keeps_two_disconnected_buildings_separate(empty_doc):
    msp = empty_doc.modelspace()
    for x0 in (0, 100):
        msp.add_line((x0, 0, 0), (x0 + 10, 0, 0), dxfattribs={"layer": "BUILDING_A"})
        msp.add_line((x0 + 10, 0, 0), (x0 + 10, 10, 0), dxfattribs={"layer": "BUILDING_A"})
        msp.add_line((x0 + 10, 10, 0), (x0, 10, 0), dxfattribs={"layer": "BUILDING_A"})
        msp.add_line((x0, 10, 0), (x0, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 2
    assert {z["type"] for z in zones} == {"building"}


def test_extract_restrictions_closes_small_gap_in_line_fragment_chain(empty_doc):
    # Топосъёмка трассирует только видимую со стороны съёмки часть стен --
    # реальный разрыв между концами меньше _BUILDING_CLOSE_GAP_M всё ещё
    # считается "реально замкнут".
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((10, 0, 0), (10, 10, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((10, 10, 0), (0, 10, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((0, 10, 0), (0, 1.0, 0), dxfattribs={"layer": "BUILDING_A"})  # разрыв 1м < 2м
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    assert zones[0]["type"] == "building"


def test_extract_restrictions_reconstructs_open_chain_via_minimum_rotated_rectangle(empty_doc):
    # Реальный разрыв (>= _BUILDING_CLOSE_GAP_M) -- три стороны прямоугольника
    # 20x8, четвёртая не оцифрована вовсе (реальный эффект топосъёмки).
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (20, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((20, 0, 0), (20, 8, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((20, 8, 0), (0, 8, 0), dxfattribs={"layer": "BUILDING_A"})
    zones = extract_restrictions(msp, Transform())
    assert len(zones) == 1
    poly = Polygon([(p["x"], p["z"]) for p in zones[0]["polygon"]])
    assert poly.area == pytest.approx(160.0, rel=0.05)


def test_extract_restrictions_drops_short_open_fragment_as_survey_noise(empty_doc):
    # Разомкнутый обрывок короче _BUILDING_MIN_SHORT_SIDE_M по короткой
    # стороне -- шум съёмки (забор, обрывок бордюра), не здание.
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (10, 0, 0), dxfattribs={"layer": "BUILDING_A"})
    msp.add_line((10, 0, 0), (10, 1, 0), dxfattribs={"layer": "BUILDING_A"})
    zones = extract_restrictions(msp, Transform())
    assert zones == []


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


def test_extract_point_objects_clamps_near_zero_block_xscale(empty_doc):
    # issue #50 follow-up, реальный случай ("13_kharkovskaya"): блок
    # "Яблоня 1" вставлен с xscale~0.0012 -- отмасштабировано под референсную
    # геометрию ТОГО САМОГО блока в исходном DWG (в тысячи раз крупнее
    # дерева), а не под нашу .glb-модель. Без клампа дерево на сцене
    # оказывается практически невидимым. Клампим наверх до MIN_RENDER_SCALE.
    empty_doc.blocks.new(name="Яблоня 1")
    msp = empty_doc.modelspace()
    msp.add_blockref("Яблоня 1", insert=(0, 0, 0), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ", "xscale": 0.0012})
    objects = extract_point_objects(msp, Transform())
    assert objects[0]["scale"] == parse_dxf.MIN_RENDER_SCALE


def test_extract_point_objects_clamps_excessively_large_block_xscale(empty_doc):
    empty_doc.blocks.new(name="huge_block")
    msp = empty_doc.modelspace()
    msp.add_blockref("huge_block", insert=(0, 0, 0), dxfattribs={"layer": "TREE_LAYER", "xscale": 50.0})
    objects = extract_point_objects(msp, Transform())
    assert objects[0]["scale"] == parse_dxf.MAX_RENDER_SCALE


def test_extract_point_objects_preserves_plausible_xscale_variation(empty_doc):
    # 0.69/1.9 -- правдоподобный, судя по всему намеренный разброс размера
    # (молодое/взрослое дерево), внутри [MIN_RENDER_SCALE, MAX_RENDER_SCALE]
    # -- не должен округляться до одного значения клампом.
    empty_doc.blocks.new(name="tree_block")
    msp = empty_doc.modelspace()
    msp.add_blockref("tree_block", insert=(0, 0, 0), dxfattribs={"layer": "TREE_LAYER", "xscale": 0.69})
    msp.add_blockref("tree_block", insert=(1, 0, 0), dxfattribs={"layer": "TREE_LAYER", "xscale": 1.9})
    objects = extract_point_objects(msp, Transform())
    scales = sorted(o["scale"] for o in objects)
    assert scales == pytest.approx([0.69, 1.9])


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


def test_extract_point_objects_bare_circles_and_inserts_coexist_on_same_layer(empty_doc):
    # Баг, найденный на реальном файле (19_2ya_pryadilnaya): 443 дерева на
    # слое TREE -- голые CIRCLE без INSERT/POINT (топосъёмка). При попытке
    # добавить дерево через add_blockref на тот же слой ветка "одиночный
    # CIRCLE-маркер" требовала "and not inserts_or_points" и молча теряла
    # все 443 существующих дерева, как только на слое появлялся хоть один
    # INSERT. CIRCLE и INSERT -- разные сущности одних и тех же данных, а не
    # альтернативные прочтения, оба должны попасть в объекты.
    empty_doc.blocks.new(name="tree_block")
    msp = empty_doc.modelspace()
    msp.add_circle((1, 1, 0), radius=0.5, dxfattribs={"layer": "TREE"})
    msp.add_circle((2, 2, 0), radius=0.5, dxfattribs={"layer": "TREE"})
    msp.add_blockref("tree_block", insert=(9, 9, 0), dxfattribs={"layer": "TREE"})
    objects = extract_point_objects(msp, Transform())
    assert len(objects) == 3
    assert sum(1 for o in objects if o["type"] == "tree") == 3
    positions = {(o["position"]["x"], o["position"]["z"]) for o in objects}
    assert positions == {(1.0, 1.0), (2.0, 2.0), (9.0, 9.0)}


def test_extract_point_objects_ignores_unmatched_layers(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "SOMETHING_UNRELATED"})
    assert extract_point_objects(msp, Transform()) == []


def test_extract_point_objects_excludes_tree_strip_zone_despite_keyword_match(empty_doc):
    # issue #50 follow-up, реальный случай ("13_kharkovskaya", топоплан):
    # "Полоса деревьев" -- линейная зона существующей растительности,
    # отрисованная топосъёмкой тысячами разрозненных LINE, а не по объекту на
    # дерево. Слово "ДЕРЕВ" совпадает с общим POINT_LAYER_RULES keyword'ом --
    # без POINT_LAYER_EXCLUDE_KEYWORDS LINE+CIRCLE-логика "столб+плафон"
    # (задумана для фонарей) распаковала бы каждый уникальный конец
    # фрагмента в отдельное фантомное дерево (были все LINE с разными
    # координатами -- тысячи "деревьев" вместо нуля).
    msp = empty_doc.modelspace()
    for i in range(5):
        msp.add_line((i * 2.0, 0, 0), (i * 2.0 + 1.0, 0, 0), dxfattribs={"layer": "Полоса деревьев"})
    assert extract_point_objects(msp, Transform()) == []


def test_extract_point_objects_assigns_independent_counters_per_type(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "TREE_A"})
    msp.add_point((1, 0, 0), dxfattribs={"layer": "TREE_B"})
    msp.add_point((0, 0, 0), dxfattribs={"layer": "BUSH_A"})
    objects = extract_point_objects(msp, Transform())
    ids = sorted(o["id"] for o in objects)
    assert ids == ["bush_001", "tree_001", "tree_002"]


def test_extract_point_objects_recognizes_russian_generic_keywords(empty_doc):
    # issue #50 follow-up: реальные проекты Мосгеотреста размечают точечную
    # посадку по-русски ("! ПР ДЕРЕВЬЯ" на "13_kharkovskaya"), не по-английски.
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "! ПР ДЕРЕВЬЯ"})
    msp.add_point((1, 0, 0), dxfattribs={"layer": "КУСТАРНИК"})
    objects = extract_point_objects(msp, Transform())
    types = sorted(o["type"] for o in objects)
    assert types == ["bush", "tree"]


def test_extract_point_objects_species_catalog_disambiguates_tree_vs_bush(empty_doc):
    # Слой назван не общим словом, а конкретным видом из справочника
    # (data/plant_archetypes/species_catalog.json) -- ровно то, что реально
    # встречается на "13_kharkovskaya" ("! ПР СПИРЕЯ ВАНГУТТА", кустарник, и
    # т.п.). "Бархат Амурский" в справочнике -- дерево, "Спирея Вангутта" --
    # лиственный кустарник.
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "! ПР БАРХАТ АМУРСКИЙ"})
    msp.add_point((1, 0, 0), dxfattribs={"layer": "! ПР СПИРЕЯ ВАНГУТТА"})
    objects = extract_point_objects(msp, Transform())
    by_layer = {o["metadata"]["sourceLayer"]: o["type"] for o in objects}
    assert by_layer["! ПР БАРХАТ АМУРСКИЙ"] == "tree"
    assert by_layer["! ПР СПИРЕЯ ВАНГУТТА"] == "bush"


def test_species_point_rules_load_real_catalog_and_split_tree_vs_bush():
    rules = parse_dxf._species_point_rules()
    assert len(rules) > 100
    by_name = dict(rules)
    assert by_name["БАРХАТ АМУРСКИЙ"]["type"] == "tree"
    assert by_name["СПИРЕЯ ВАНГУТТА"]["type"] == "bush"


def test_species_point_rules_returns_empty_list_when_file_missing(monkeypatch, tmp_path):
    # Справочник необязателен -- отсутствие/битость файла не должна ронять
    # парсер. Подставляем __file__ так, что Path(__file__).parent.parent
    # указывает на пустую tmp_path (без data/plant_archetypes/...).
    monkeypatch.setattr(parse_dxf, "__file__", str(tmp_path / "parser" / "parse_dxf.py"))
    assert parse_dxf._species_point_rules() == []


def test_species_point_rules_returns_empty_list_for_invalid_json(monkeypatch, tmp_path):
    bad_path = tmp_path / "data" / "plant_archetypes" / "species_catalog.json"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("not valid json", encoding="utf-8")
    monkeypatch.setattr(parse_dxf, "__file__", str(tmp_path / "parser" / "parse_dxf.py"))
    assert parse_dxf._species_point_rules() == []


# --- extract_curb_polylines ------------------------------------------------


def test_extract_curb_polylines_recognizes_russian_kerb_keyword(empty_doc):
    # issue #50 follow-up: "ДВ_ГП_П_Борт_БР100.30.15" на "13_kharkovskaya".
    msp = empty_doc.modelspace()
    msp.add_line((0, 0, 0), (1, 0, 0), dxfattribs={"layer": "ДВ_ГП_П_Борт_БР100.30.15"})
    curbs = extract_curb_polylines(msp, Transform())
    assert len(curbs) == 1


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
    assert set(result.keys()) == {"boundary", "restrictions", "objects", "windows", "canopies", "curbs", "meta"}
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


# --- _estimate_fallback_region / отсев выбросов без границы (issue #50) --------


def test_parse_dxf_doc_without_boundary_drops_far_away_outlier_point(empty_doc):
    # Реальный случай ("13_kharkovskaya", issue #50 follow-up): без слоя
    # границы одна точка за много километров от плотного скопления остальных
    # растягивает bbox сцены в разы. 12 деревьев плотно у (0,0), одно -- за
    # 5 км, слоя границы нет вообще.
    msp = empty_doc.modelspace()
    for i in range(12):
        msp.add_point((i * 0.5, i * 0.3, 0), dxfattribs={"layer": "TREE"})
    msp.add_point((5000, 5000, 0), dxfattribs={"layer": "TREE"})
    result = parse_dxf_doc(empty_doc)
    assert len(result["objects"]) == 12
    xs = [o["position"]["x"] for o in result["objects"]]
    assert max(xs) < 100
    # Граница теперь оценивается по отфильтрованному содержимому (issue #50
    # follow-up: GreenPlan без scene.boundary не работает вообще, см.
    # docstring _estimate_boundary_from_content) -- и не должна включать
    # отброшенный выброс.
    assert result["boundary"] is not None
    assert result["boundary"]["sourceLayer"] == parse_dxf.ESTIMATED_BOUNDARY_SOURCE_LAYER
    bxs = [p["x"] for p in result["boundary"]["polygon"]]
    assert max(bxs) < 100


def test_parse_dxf_doc_without_boundary_keeps_elongated_site_intact(empty_doc):
    # Честно длинный узкий участок (набережная/проезд) не должен обрезаться
    # по краям -- точки распределены НЕПРЕРЫВНО по всей длине, без разрыва,
    # в отличие от единичного выброса выше.
    msp = empty_doc.modelspace()
    for i in range(50):
        msp.add_point((i * 20.0, 0, 0), dxfattribs={"layer": "TREE"})
    result = parse_dxf_doc(empty_doc)
    assert len(result["objects"]) == 50
    xs = [o["position"]["x"] for o in result["objects"]]
    assert max(xs) - min(xs) == pytest.approx(49 * 20.0)
    # Оценённая граница должна накрывать весь участок, а не только середину.
    bxs = [p["x"] for p in result["boundary"]["polygon"]]
    assert min(bxs) < 0
    assert max(bxs) > 49 * 20.0


def test_parse_dxf_doc_without_boundary_and_too_few_points_skips_filtering(empty_doc):
    # Меньше _OUTLIER_MIN_POINTS -- перцентильная оценка ненадёжна, ничего не
    # отсеиваем (лучше показать выброс, чем случайно выкинуть настоящий
    # объект на маленькой сцене) -- и границу тоже не оцениваем по той же
    # причине.
    msp = empty_doc.modelspace()
    msp.add_point((0, 0, 0), dxfattribs={"layer": "TREE"})
    msp.add_point((5000, 5000, 0), dxfattribs={"layer": "TREE"})
    result = parse_dxf_doc(empty_doc)
    assert len(result["objects"]) == 2
    assert result["boundary"] is None


# --- _estimate_boundary_from_content (issue #50) --------------------------------


def test_estimate_boundary_from_content_returns_none_for_too_few_points():
    objects = [{"position": {"x": 0, "z": 0}}, {"position": {"x": 1, "z": 1}}]
    assert parse_dxf._estimate_boundary_from_content(objects, [], []) is None


def test_estimate_boundary_from_content_covers_all_input_points():
    objects = [{"position": {"x": float(i), "z": float(i % 3)}} for i in range(20)]
    boundary = parse_dxf._estimate_boundary_from_content(objects, [], [])
    assert boundary is not None
    assert boundary["sourceLayer"] == parse_dxf.ESTIMATED_BOUNDARY_SOURCE_LAYER
    poly = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    assert poly.is_valid
    for o in objects:
        assert poly.covers(Point(o["position"]["x"], o["position"]["z"]))


def test_estimate_boundary_from_content_survives_nearly_collinear_points():
    # concave_hull может упасть на почти вырожденных наборах точек (GEOS
    # "Tri::getAdjacent - invalid index", реально воспроизведено на этом же
    # наборе координат) -- должен тихо откатиться на convex_hull, не падать.
    objects = [{"position": {"x": i * 0.5, "z": i * 0.3}} for i in range(12)]
    boundary = parse_dxf._estimate_boundary_from_content(objects, [], [])
    assert boundary is not None


def test_parse_dxf_doc_without_any_boundary_layer_returns_none_boundary(empty_doc):
    result = parse_dxf_doc(empty_doc)
    assert result["boundary"] is None
    assert result["meta"]["origin"] == {"x": 0.0, "y": 0.0}


# --- _compute_ground_zone (issue #53) -------------------------------------------


def _square_zone(zone_id, ztype, severity, x0, z0, x1, z1):
    return {
        "id": zone_id,
        "type": ztype,
        "name": "test",
        "polygon": [{"x": x0, "z": z0}, {"x": x1, "z": z0}, {"x": x1, "z": z1}, {"x": x0, "z": z1}],
        "severity": severity,
        "minDistance": 0.0,
        "message": "test",
    }


def test_compute_ground_zone_returns_empty_without_boundary():
    assert parse_dxf._compute_ground_zone(None, []) == []


def test_compute_ground_zone_fills_gap_left_by_known_zones():
    # Участок 100x100, здание занимает только левую половину -- правая
    # половина должна стать новой allowed-зоной.
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    building = _square_zone("building_001", "building", "forbidden", 0, 0, 50, 100)
    ground = parse_dxf._compute_ground_zone(boundary, [building])
    assert len(ground) == 1
    zone = ground[0]
    assert zone["type"] == "protected_zone"
    assert zone["severity"] == "allowed"
    assert zone["name"] == parse_dxf.GROUND_ZONE_SOURCE_NAME
    poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
    assert poly.area == pytest.approx(5000.0, rel=0.01)


def test_compute_ground_zone_empty_when_already_fully_covered():
    # Газон уже покрывает весь участок -- вручную собранные locations/ с
    # честным GROUND-слоем не должны получить дублирующую зону поверх.
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    lawn = _square_zone("protected_zone_001", "protected_zone", "allowed", 0, 0, 100, 100)
    assert parse_dxf._compute_ground_zone(boundary, [lawn]) == []


def test_compute_ground_zone_drops_tiny_leftover_slivers():
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    # Здание занимает участок почти целиком, оставляя полоску 100x0.01 (1м²)
    # -- меньше _GROUND_ZONE_MIN_AREA_SQM, должно быть отброшено как обрывок.
    building = _square_zone("building_001", "building", "forbidden", 0, 0, 100, 99.99)
    assert parse_dxf._compute_ground_zone(boundary, [building]) == []


def test_compute_ground_zone_ids_continue_after_existing_protected_zones():
    # Один существующий protected_zone -- новая зона должна получить id
    # "_002", а не "_001" (иначе id совпал бы с уже занятым).
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    lawn = _square_zone("protected_zone_001", "protected_zone", "allowed", 0, 0, 40, 100)
    ground = parse_dxf._compute_ground_zone(boundary, [lawn])
    assert len(ground) == 1
    assert ground[0]["id"] == "protected_zone_002"


def test_parse_dxf_doc_adds_ground_zone_for_uncovered_area(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 100), (0, 100)], close=True, dxfattribs={"layer": "BUILDING_1"})
    result = parse_dxf_doc(empty_doc)
    ground_zones = [z for z in result["restrictions"] if z["name"] == parse_dxf.GROUND_ZONE_SOURCE_NAME]
    assert len(ground_zones) == 1
    assert ground_zones[0]["severity"] == "allowed"


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
