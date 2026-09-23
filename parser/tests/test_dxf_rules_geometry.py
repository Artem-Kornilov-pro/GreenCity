"""dxf_parsing/rules.py и dxf_parsing/geometry.py -- распознавание слоёв и
геометрические примитивы парсера, на минимальных сущностях ezdxf.new()."""


import pytest

from parse_dxf import (
    POLYGON_RULES,
    Transform,
    buffer_segment,
    centroid,
    clean_label,
    layer_matches,
    match_rule,
    nearest_text,
    polygon_points,
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
