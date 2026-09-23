"""dxf_parsing/zones.py и dxf_parsing/boundary_estimate.py -- граница участка,
зоны ограничений, открытая земля, оценка границы без её слоя."""


import pytest
from shapely.geometry import Point, Polygon

from dxf_parsing import boundary_estimate, zones
from parse_dxf import (
    Transform,
    extract_boundary,
    extract_restrictions,
    parse_dxf_doc,
    parse_dxf_file,
)

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
    assert result["boundary"]["sourceLayer"] == boundary_estimate.ESTIMATED_BOUNDARY_SOURCE_LAYER
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
    assert boundary_estimate._estimate_boundary_from_content(objects, [], []) is None


def test_estimate_boundary_from_content_covers_all_input_points():
    objects = [{"position": {"x": float(i), "z": float(i % 3)}} for i in range(20)]
    boundary = boundary_estimate._estimate_boundary_from_content(objects, [], [])
    assert boundary is not None
    assert boundary["sourceLayer"] == boundary_estimate.ESTIMATED_BOUNDARY_SOURCE_LAYER
    poly = Polygon([(p["x"], p["z"]) for p in boundary["polygon"]])
    assert poly.is_valid
    for o in objects:
        assert poly.covers(Point(o["position"]["x"], o["position"]["z"]))


def test_estimate_boundary_from_content_survives_nearly_collinear_points():
    # concave_hull может упасть на почти вырожденных наборах точек (GEOS
    # "Tri::getAdjacent - invalid index", реально воспроизведено на этом же
    # наборе координат) -- должен тихо откатиться на convex_hull, не падать.
    objects = [{"position": {"x": i * 0.5, "z": i * 0.3}} for i in range(12)]
    boundary = boundary_estimate._estimate_boundary_from_content(objects, [], [])
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
    assert zones._compute_ground_zone(None, []) == []


def test_compute_ground_zone_fills_gap_left_by_known_zones():
    # Участок 100x100, здание занимает только левую половину -- правая
    # половина должна стать новой allowed-зоной.
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    building = _square_zone("building_001", "building", "forbidden", 0, 0, 50, 100)
    ground = zones._compute_ground_zone(boundary, [building])
    assert len(ground) == 1
    zone = ground[0]
    assert zone["type"] == "protected_zone"
    assert zone["severity"] == "allowed"
    assert zone["name"] == zones.GROUND_ZONE_SOURCE_NAME
    poly = Polygon([(p["x"], p["z"]) for p in zone["polygon"]])
    assert poly.area == pytest.approx(5000.0, rel=0.01)


def test_compute_ground_zone_empty_when_already_fully_covered():
    # Газон уже покрывает весь участок -- вручную собранные locations/ с
    # честным GROUND-слоем не должны получить дублирующую зону поверх.
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    lawn = _square_zone("protected_zone_001", "protected_zone", "allowed", 0, 0, 100, 100)
    assert zones._compute_ground_zone(boundary, [lawn]) == []


def test_compute_ground_zone_drops_tiny_leftover_slivers():
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    # Здание занимает участок почти целиком, оставляя полоску 100x0.01 (1м²)
    # -- меньше _GROUND_ZONE_MIN_AREA_SQM, должно быть отброшено как обрывок.
    building = _square_zone("building_001", "building", "forbidden", 0, 0, 100, 99.99)
    assert zones._compute_ground_zone(boundary, [building]) == []


def test_compute_ground_zone_ids_continue_after_existing_protected_zones():
    # Один существующий protected_zone -- новая зона должна получить id
    # "_002", а не "_001" (иначе id совпал бы с уже занятым).
    boundary = {"polygon": [{"x": 0, "z": 0}, {"x": 100, "z": 0}, {"x": 100, "z": 100}, {"x": 0, "z": 100}]}
    lawn = _square_zone("protected_zone_001", "protected_zone", "allowed", 0, 0, 40, 100)
    ground = zones._compute_ground_zone(boundary, [lawn])
    assert len(ground) == 1
    assert ground[0]["id"] == "protected_zone_002"


def test_parse_dxf_doc_adds_ground_zone_for_uncovered_area(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 100), (0, 100)], close=True, dxfattribs={"layer": "BUILDING_1"})
    result = parse_dxf_doc(empty_doc)
    ground_zones = [z for z in result["restrictions"] if z["name"] == zones.GROUND_ZONE_SOURCE_NAME]
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
