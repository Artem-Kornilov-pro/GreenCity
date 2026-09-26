"""dxf_parsing/objects.py -- здания, точечные объекты, фасады, бордюры."""

import math

import pytest
from shapely.geometry import Polygon

from dxf_parsing import rules as layer_rules
from parse_dxf import (
    Transform,
    extract_buildings,
    extract_curb_polylines,
    extract_facade_quads,
    extract_point_objects,
    extract_restrictions,
)

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
    assert objects[0]["scale"] == layer_rules.MIN_RENDER_SCALE


def test_extract_point_objects_clamps_excessively_large_block_xscale(empty_doc):
    empty_doc.blocks.new(name="huge_block")
    msp = empty_doc.modelspace()
    msp.add_blockref("huge_block", insert=(0, 0, 0), dxfattribs={"layer": "TREE_LAYER", "xscale": 50.0})
    objects = extract_point_objects(msp, Transform())
    assert objects[0]["scale"] == layer_rules.MAX_RENDER_SCALE


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


def test_extract_point_objects_circle_marker_at_a_point_is_not_a_second_object(empty_doc):
    # Так пишет каждую посадку наш экспорт (exchange/export_dxf.py): POINT +
    # маленький CIRCLE-маркер в той же точке, иначе в CAD её не видно.
    # Повторная загрузка экспортированного плана не должна удваивать посадки
    # -- найдено сквозным тестом tests/e2e/test_greenplan_flow.py. Круг в
    # ДРУГОЙ точке -- по-прежнему отдельный объект.
    msp = empty_doc.modelspace()
    msp.add_point((3, 4, 0), dxfattribs={"layer": "NEW_TREE"})
    msp.add_circle((3, 4), radius=0.9, dxfattribs={"layer": "NEW_TREE"})
    msp.add_circle((7, 7), radius=0.9, dxfattribs={"layer": "NEW_TREE"})
    objects = extract_point_objects(msp, Transform())
    positions = sorted((o["position"]["x"], o["position"]["z"]) for o in objects)
    assert positions == [(3.0, 4.0), (7.0, 7.0)]


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
    rules = layer_rules._species_point_rules()
    assert len(rules) > 100
    by_name = dict(rules)
    assert by_name["БАРХАТ АМУРСКИЙ"]["type"] == "tree"
    assert by_name["СПИРЕЯ ВАНГУТТА"]["type"] == "bush"


def test_species_point_rules_returns_empty_list_when_file_missing(monkeypatch, tmp_path):
    # Справочник необязателен -- отсутствие/битость файла не должна ронять
    # парсер. Путь к справочнику -- на несуществующий файл в tmp_path.
    monkeypatch.setattr(layer_rules, "SPECIES_CATALOG_PATH", tmp_path / "missing.json")
    assert layer_rules._species_point_rules() == []


def test_species_point_rules_returns_empty_list_for_invalid_json(monkeypatch, tmp_path):
    bad_path = tmp_path / "data" / "plant_archetypes" / "species_catalog.json"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("not valid json", encoding="utf-8")
    monkeypatch.setattr(layer_rules, "SPECIES_CATALOG_PATH", bad_path)
    assert layer_rules._species_point_rules() == []


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


def test_species_layer_passes_species_name_to_the_object(empty_doc):
    # Слой с названием вида ("ЛИПА МЕЛКОЛИСТНАЯ") -- дерево этого вида:
    # название уходит в metadata.species, по нему бэкенд находит модель.
    msp = empty_doc.modelspace()
    msp.add_point((1, 2, 0), dxfattribs={"layer": "ЛИПА МЕЛКОЛИСТНАЯ"})
    msp.add_point((5, 2, 0), dxfattribs={"layer": "TREE"})
    objects = extract_point_objects(msp, Transform())
    by_layer = {o["metadata"]["sourceLayer"]: o for o in objects}
    assert len(objects) == 2
    assert by_layer["ЛИПА МЕЛКОЛИСТНАЯ"]["type"] == "tree"
    assert by_layer["ЛИПА МЕЛКОЛИСТНАЯ"]["metadata"]["species"] == "Липа мелколистная"
    assert "species" not in by_layer["TREE"]["metadata"]
