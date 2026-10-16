"""Чертёж от заказчика разбирается так же, как эталонный, как бы его ни
оформили: русские, английские и сокращённые имена слоёв, внешние ссылки,
латиница в русских словах, весь план или каждый слой одним блоком,
миллиметры без единиц в заголовке, абсолютные отметки, кракозябры в именах.

Берутся настоящие чертежи (тестовые участки и реальный проект), портятся так,
как это делают заказчики, и сцена сравнивается с разбором исходника: те же
граница, площади зон по типам, объекты по типам и видам, высоты зданий."""

import collections
import io
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Matrix44
from shapely.geometry import Polygon
from shapely.ops import unary_union

from parse_dxf import parse_dxf_doc, read_dxf

ROOT = Path(__file__).resolve().parents[2]
SITES = [
    ROOT / "locations" / "location_test" / "01_school" / "01_school.dxf",
    ROOT / "locations" / "location_test" / "03_courtyard_renovation" / "03_courtyard_renovation.dxf",
    ROOT / "locations" / "12_natashinsky_proezd" / "12_natashinsky_proezd.dxf",
]

# Как слои называют заказчики.
RUSSIAN = {
    "TERRITORY_BOUNDARY": "Граница участка", "BOUNDARY": "Граница ЗУ", "BUILDING_FOOTPRINT": "Строения",
    "BUILDING": "Жилые дома", "BUILDING_3D": "Строения 3D", "ENTRANCES": "Входы", "ROAD": "Проезды",
    "SIDEWALK": "Тротуары", "PATHS": "Пешеходные дорожки", "PARKING": "Автостоянка",
    "PARKING_MARKINGS": "Разметка парковки", "PLAYGROUND": "Детская площадка", "PLAYGROUND_SPORTS": "Спортплощадка",
    "GRASS": "Травяной покров", "TRANSFORMER_SUBSTATION": "ТП-1234", "WATER_SUPPLY_B1": "В1",
    "WATER_PIPELINE": "Водоснабжение", "SEWER_K1": "К1", "SEWER": "Канализ. бытовая", "HEATING_T1": "Тепловые сети",
    "HEAT": "Т1", "GAS_PIPE": "Газ_НД", "POWER_CABLE": "КЛ 0,4 кВ", "ELECTR_CABLE": "Эл.сети",
    "CABLE_COMM": "Слаботочные сети", "ELECTR_CABLE_COMM": "Телефон", "OVERHEAD_POWER_LINE": "ВЛ 10кВ",
    "LAMPS": "Опоры освещения", "BENCHES": "Скамьи", "LABELS": "Подписи", "CURB": "Бортовой камень",
    "TREE": "Деревья", "BUSH": "Кустарники", "LOUNGER": "Шезлонги",
}
ENGLISH = {
    "TERRITORY_BOUNDARY": "Plot", "BOUNDARY": "Plot", "BUILDING_FOOTPRINT": "Bldg", "BUILDING": "Bldg",
    "BUILDING_3D": "Bldg3D", "ENTRANCES": "Doors", "ROAD": "Streets", "SIDEWALK": "Footway", "PATHS": "Footway",
    "PARKING": "Car Park", "PARKING_MARKINGS": "Road Markings", "PLAYGROUND": "Play Area",
    "PLAYGROUND_SPORTS": "Sports Court", "GRASS": "Turf", "TRANSFORMER_SUBSTATION": "Substation",
    "WATER_SUPPLY_B1": "WaterMain", "WATER_PIPELINE": "WaterMain", "SEWER_K1": "StormDrain", "SEWER": "StormDrain",
    "HEATING_T1": "DistrictHeating", "HEAT": "DistrictHeating", "GAS_PIPE": "GasLine", "POWER_CABLE": "PowerCable",
    "ELECTR_CABLE": "PowerCable", "CABLE_COMM": "Telecom", "ELECTR_CABLE_COMM": "Telecom",
    "OVERHEAD_POWER_LINE": "OverheadLine", "LAMPS": "StreetLights", "BENCHES": "Seating", "LABELS": "Annotation",
    "CURB": "Kerb", "TREE": "ExistingTrees", "BUSH": "Hedges", "LOUNGER": "Loungers",
}
HOMOGLYPHS = str.maketrans("оаерсОАЕРС", "oaepcOAEPC")


def russian(layer):
    if layer.startswith("EXISTING_TREE_"):
        return "Деревья сущ. " + layer.removeprefix("EXISTING_TREE_")
    if layer.startswith("EXISTING_BUSH_"):
        return "Кустарник сущ. " + layer.removeprefix("EXISTING_BUSH_")
    return RUSSIAN.get(layer, layer)


def english(layer):
    if layer.startswith("EXISTING_TREE_"):
        return "Trees " + layer.removeprefix("EXISTING_TREE_")
    if layer.startswith("EXISTING_BUSH_"):
        return "Shrubs " + layer.removeprefix("EXISTING_BUSH_")
    return ENGLISH.get(layer, layer)


def renamed(doc, name_of):
    for e in doc.modelspace():
        e.dxf.layer = name_of(e.dxf.layer)
    return doc


def as_one_block(doc):
    msp = doc.modelspace()
    block = doc.blocks.new("ПОДОСНОВА")
    for e in list(msp):
        msp.move_to_layout(e, block)
    msp.add_blockref("ПОДОСНОВА", (0, 0), dxfattribs={"layer": "0"})
    return doc


def block_per_layer(doc, insert_on_layer):
    """Каждый слой -- блок с сущностями на «0»: вставка на слое «0» с именем
    блока по слою или вставка на самом слое."""
    msp = doc.modelspace()
    by_layer = collections.defaultdict(list)
    for e in list(msp):
        by_layer[e.dxf.layer].append(e)
    for i, (layer, entities) in enumerate(by_layer.items()):
        name = f"BLK_{i}" if insert_on_layer else layer
        block = doc.blocks.new(name)
        for e in entities:
            msp.move_to_layout(e, block)
            e.dxf.layer = "0"
        msp.add_blockref(name, (0, 0), dxfattribs={"layer": layer if insert_on_layer else "0"})
    return doc


def transformed(doc, matrix, insunits=None):
    for e in doc.modelspace():
        e.transform(matrix)
    if insunits is not None:
        doc.header["$INSUNITS"] = insunits
    return doc


MUTATIONS = {
    "русские имена": lambda d: renamed(d, russian),
    "английские имена": lambda d: renamed(d, english),
    "внешняя ссылка": lambda d: renamed(d, lambda layer: "Топоплан|" + russian(layer)),
    "привязанная внешняя ссылка": lambda d: renamed(d, lambda layer: "GENPLAN$0$" + layer),
    "латиница в русских словах": lambda d: renamed(d, lambda layer: russian(layer).translate(HOMOGLYPHS)),
    "весь план блоком на слое 0": lambda d: as_one_block(renamed(d, russian)),
    "слой -- блок с именем слоя": lambda d: block_per_layer(renamed(d, russian), insert_on_layer=False),
    "слой -- блок на своём слое": lambda d: block_per_layer(renamed(d, russian), insert_on_layer=True),
    "миллиметры без единиц": lambda d: transformed(renamed(d, russian), Matrix44.scale(1000, 1000, 1000), insunits=0),
    "абсолютные отметки": lambda d: transformed(renamed(d, russian), Matrix44.translate(0, 0, 150)),
}


def summary(scene):
    def polygon(points):
        poly = Polygon([(p["x"], p["z"]) for p in points])
        return poly if poly.is_valid else poly.buffer(0)

    zones = collections.defaultdict(list)
    for zone in scene["restrictions"]:
        if len(zone["polygon"]) >= 3:
            zones[(zone["type"], zone["severity"])].append(polygon(zone["polygon"]))
    return {
        "boundary": polygon(scene["boundary"]["polygon"]).area if scene["boundary"] else None,
        "zones": {key: unary_union(polys).area for key, polys in zones.items()},
        "objects": collections.Counter(o["type"] for o in scene["objects"]),
        "species": collections.Counter(o["metadata"].get("species") for o in scene["objects"] if o["type"] in ("tree", "bush")),
        "heights": sorted(round(o["metadata"].get("height") or 0, 1) for o in scene["objects"] if o["type"] == "building"),
        "max_y": max((o["position"]["y"] for o in scene["objects"] if o["type"] != "building"), default=0),
    }


_ORIGINALS = {}


def original(path):
    if path not in _ORIGINALS:
        _ORIGINALS[path] = summary(parse_dxf_doc(ezdxf.readfile(path)))
    return _ORIGINALS[path]


@pytest.mark.parametrize("path", SITES, ids=lambda p: p.stem)
@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_customer_style_drawing_parses_like_the_original(path, mutation):
    expected = original(path)
    scene = parse_dxf_doc(MUTATIONS[mutation](ezdxf.readfile(path)))
    got = summary(scene)

    assert got["boundary"] == pytest.approx(expected["boundary"], rel=0.01)
    assert set(got["zones"]) == set(expected["zones"])
    for key, area in expected["zones"].items():
        assert got["zones"][key] == pytest.approx(area, rel=0.02), key
    assert got["objects"] == expected["objects"]
    assert got["species"] == expected["species"]
    assert got["heights"] == pytest.approx(expected["heights"], abs=0.1)
    assert got["max_y"] == pytest.approx(expected["max_y"], abs=0.5)
    assert [layer["name"] for layer in scene["meta"]["layers"] if layer["label"] is None] in ([], ["0"])


# --- единицы чертежа ------------------------------------------------------------


def _site(doc, size, units):
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (size, 0), (size, size), (0, size)], close=True, dxfattribs={"layer": "Граница участка"})
    doc.header["$INSUNITS"] = units
    return doc


def test_millimetres_without_units_in_the_header_are_detected(empty_doc):
    scene = parse_dxf_doc(_site(empty_doc, 80_000, 0))
    assert scene["meta"]["scale"] == 0.001
    assert any("миллиметры" in w for w in scene["meta"]["warnings"])


def test_metres_declared_as_millimetres_are_detected(empty_doc):
    scene = parse_dxf_doc(_site(empty_doc, 80, 4))
    assert scene["meta"]["scale"] == 1.0


def test_plausible_header_units_are_kept(empty_doc):
    scene = parse_dxf_doc(_site(empty_doc, 80_000, 4))
    assert scene["meta"]["scale"] == 0.001
    assert scene["meta"]["warnings"] == []


def test_network_corridor_has_the_same_width_in_metres_in_a_millimetre_drawing(empty_doc):
    _site(empty_doc, 80_000, 4)
    empty_doc.modelspace().add_line((0, 40_000), (80_000, 40_000), dxfattribs={"layer": "WATER_PIPELINE"})
    zone = next(z for z in parse_dxf_doc(empty_doc)["restrictions"] if z["type"] == "water_pipeline")
    zs = [p["z"] for p in zone["polygon"]]
    assert max(zs) - min(zs) == pytest.approx(6.0, abs=0.01)  # 2 x 3 м охранной зоны


# --- отметки, кривые, условные знаки ------------------------------------------------


def test_objects_on_absolute_elevations_stand_on_the_ground(empty_doc):
    msp = empty_doc.modelspace()
    _site(empty_doc, 100, 6)
    msp.add_circle((50, 50, 152.0), 1.0, dxfattribs={"layer": "ENTRANCES"})
    msp.add_point((30, 30, 150.0), dxfattribs={"layer": "Деревья"})
    msp.add_point((60, 30, 150.0), dxfattribs={"layer": "Деревья"})
    entrance = next(o for o in parse_dxf_doc(empty_doc)["objects"] if o["type"] == "entrance")
    assert entrance["position"]["y"] == pytest.approx(2.0)


def test_arcs_splines_and_circles_on_zone_layers_become_zones(empty_doc):
    msp = empty_doc.modelspace()
    _site(empty_doc, 100, 6)
    msp.add_arc((50, 50), 30, 0, 90, dxfattribs={"layer": "Газопровод"})
    msp.add_spline([(0, 10), (30, 15), (60, 5), (100, 10)], dxfattribs={"layer": "Водопровод"})
    msp.add_circle((20, 80), 6, dxfattribs={"layer": "Газон"})
    msp.add_circle((80, 80), 0.5, dxfattribs={"layer": "Канализация"})  # колодец -- не площадь
    # Круг из двух вершин с дугами -- частый способ нарисовать клумбу.
    msp.add_lwpolyline([(70, 20, 1), (80, 20, 1)], format="xyb", close=True, dxfattribs={"layer": "Газон"})
    types = collections.Counter(z["type"] for z in parse_dxf_doc(empty_doc)["restrictions"] if z["name"] != "__computed_ground__")
    assert types["gas_pipeline"] == 1
    assert types["water_pipeline"] == 1
    assert types["protected_zone"] == 2
    assert types["sewer"] == 0


def test_tree_symbol_drawn_as_circle_with_a_cross_is_one_tree(empty_doc):
    msp = empty_doc.modelspace()
    _site(empty_doc, 100, 6)
    for x in (20, 50, 80):
        msp.add_circle((x, 50), 2, dxfattribs={"layer": "Деревья"})
        msp.add_line((x - 2, 50), (x + 2, 50), dxfattribs={"layer": "Деревья"})
        msp.add_line((x, 48), (x, 52), dxfattribs={"layer": "Деревья"})
    trees = [o for o in parse_dxf_doc(empty_doc)["objects"] if o["type"] == "tree"]
    assert sorted(o["position"]["x"] for o in trees) == [-30.0, 0.0, 30.0]


# --- замечания разбора и кодировка ----------------------------------------------


def test_estimated_boundary_and_unrecognized_layers_are_reported(empty_doc):
    msp = empty_doc.modelspace()
    for x in range(0, 100, 10):
        msp.add_point((x, x), dxfattribs={"layer": "Деревья"})
    msp.add_line((0, 0), (5, 5), dxfattribs={"layer": "Слой 7"})
    warnings = parse_dxf_doc(empty_doc)["meta"]["warnings"]
    assert any("Слой границы участка не найден" in w for w in warnings)
    assert any("Не распознано слоёв: 1" in w for w in warnings)


def test_names_written_in_utf8_under_a_cp1252_codepage_are_read_correctly(tmp_path):
    doc = ezdxf.new("R2000")
    _site(doc, 100, 6)
    doc.modelspace().add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True, dxfattribs={"layer": "Здания"})
    text = io.StringIO()
    doc.write(text)
    assert "ANSI_1252" in text.getvalue()
    path = tmp_path / "utf8.dxf"
    path.write_bytes(text.getvalue().encode("utf-8"))  # как у dwg2dxf: заявлена ANSI_1252, записано в UTF-8
    plain = ezdxf.readfile(path)
    assert "Здания" not in {e.dxf.layer for e in plain.modelspace()}
    scene = parse_dxf_doc(read_dxf(path))
    assert [o["metadata"]["sourceLayer"] for o in scene["objects"]] == ["Здания"]
    assert scene["boundary"]["sourceLayer"] == "Граница участка"


# --- тестовый участок «как от заказчика» ------------------------------------------


def test_customer_layers_site_parses_like_its_original_once_one_layer_is_assigned():
    # locations/location_test/04_customer_layers -- двор 03, оформленный так,
    # как присылают заказчики (generator/build_dxf.py). Всё, кроме «Слоя 7» с
    # подстанцией, распознаётся само; с назначением -- та же сцена, что у 03.
    customer = ROOT / "locations" / "location_test" / "04_customer_layers" / "04_customer_layers.dxf"
    expected = original(SITES[1])

    auto = parse_dxf_doc(ezdxf.readfile(customer))
    assert [layer["name"] for layer in auto["meta"]["layers"] if layer["label"] is None] == ["Слой 7"]
    assert auto["meta"]["scale"] == 0.001
    assert ("transformer", "forbidden") not in summary(auto)["zones"]

    got = summary(parse_dxf_doc(ezdxf.readfile(customer), layer_map={"Слой 7": "transformer"}))
    assert got["boundary"] == pytest.approx(expected["boundary"], rel=0.01)
    assert set(got["zones"]) == set(expected["zones"])
    for key, area in expected["zones"].items():
        assert got["zones"][key] == pytest.approx(area, rel=0.02), key
    assert got["objects"] == expected["objects"]
    assert got["species"] == expected["species"]
    assert got["heights"] == pytest.approx(expected["heights"], abs=0.1)
