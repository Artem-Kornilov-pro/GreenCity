"""dxf_parsing/drawing.py -- раскрытие вставок блоков, сводка слоёв и отбор
сущностей до разбора (пачка DWG)."""

from dxf_parsing.drawing import DrawingView, ParsedContentFilter
from dxf_parsing.layer_names import LayerResolver
from parse_dxf import parse_dxf_doc


def _square(layout, x, y, size, layer):
    layout.add_lwpolyline([(x, y), (x + size, y), (x + size, y + size), (x, y + size)], close=True, dxfattribs={"layer": layer})


def test_plan_inserted_as_a_block_on_layer_zero_is_parsed_like_a_plain_drawing(empty_doc):
    plan = empty_doc.blocks.new("ПОДОСНОВА")
    _square(plan, 0, 0, 100, "Граница участка")
    _square(plan, 20, 20, 15, "Здания")
    plan.add_point((60, 60), dxfattribs={"layer": "Деревья"})
    empty_doc.modelspace().add_blockref("ПОДОСНОВА", (1000, 2000), dxfattribs={"layer": "0"})

    scene = parse_dxf_doc(empty_doc)

    assert scene["boundary"]["sourceLayer"] == "Граница участка"
    assert [o["type"] for o in scene["objects"]] == ["building", "tree"]
    assert scene["meta"]["origin"] == {"x": 1050.0, "y": 2050.0}
    assert any("Раскрыто вставок блоков: 1" in w for w in scene["meta"]["warnings"])


def test_entities_on_layer_zero_take_the_layer_of_the_insert(empty_doc):
    block = empty_doc.blocks.new("ДОМ")
    _square(block, 0, 0, 10, "0")
    empty_doc.modelspace().add_blockref("ДОМ", (5, 5), dxfattribs={"layer": "Здания"})
    view = DrawingView(empty_doc.modelspace())
    assert [(e.dxftype(), e.dxf.layer) for e in view] == [("LWPOLYLINE", "Здания")]


def test_entities_on_layer_zero_take_the_block_name_when_the_insert_layer_says_nothing(empty_doc):
    block = empty_doc.blocks.new("Здание_5")
    _square(block, 0, 0, 10, "0")
    empty_doc.modelspace().add_blockref("Здание_5", (5, 5), dxfattribs={"layer": "0"})
    view = DrawingView(empty_doc.modelspace())
    assert [e.dxf.layer for e in view] == ["Здание_5"]


def test_symbol_insert_stays_one_object(empty_doc):
    block = empty_doc.blocks.new("ДЕРЕВО")
    block.add_circle((0, 0), 2)
    block.add_line((-2, 0), (2, 0))
    block.add_line((0, -2), (0, 2))
    empty_doc.modelspace().add_blockref("ДЕРЕВО", (5, 5), dxfattribs={"layer": "Деревья"})
    view = DrawingView(empty_doc.modelspace())
    assert [e.dxftype() for e in view] == ["INSERT"]
    assert view.exploded_inserts == 0


def test_block_with_many_points_on_a_symbol_layer_is_a_group_and_is_exploded(empty_doc):
    block = empty_doc.blocks.new("АЛЛЕЯ")
    for i in range(5):
        block.add_point((i * 6, 0))
    empty_doc.modelspace().add_blockref("АЛЛЕЯ", (0, 0), dxfattribs={"layer": "Деревья"})
    view = DrawingView(empty_doc.modelspace())
    assert [e.dxftype() for e in view] == ["POINT"] * 5
    assert {e.dxf.layer for e in view} == {"Деревья"}


def test_concentric_circles_are_one_symbol_not_a_group(empty_doc):
    block = empty_doc.blocks.new("ЕЛЬ")
    block.add_circle((0, 0), 3)
    block.add_circle((0, 0), 0.3)
    empty_doc.modelspace().add_blockref("ЕЛЬ", (0, 0), dxfattribs={"layer": "Деревья"})
    assert [e.dxftype() for e in DrawingView(empty_doc.modelspace())] == ["INSERT"]


def test_nested_blocks_are_exploded_recursively_with_transforms(empty_doc):
    inner = empty_doc.blocks.new("INNER")
    inner.add_point((1, 0), dxfattribs={"layer": "Фонари"})
    outer = empty_doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (10, 0))
    empty_doc.modelspace().add_blockref("OUTER", (100, 100), dxfattribs={"layer": "0", "rotation": 90})
    points = [e for e in DrawingView(empty_doc.modelspace()) if e.dxftype() == "POINT"]
    assert len(points) == 1
    loc = points[0].dxf.location
    assert (round(loc.x, 6), round(loc.y, 6)) == (100.0, 111.0)


def test_paperspace_is_parsed_when_model_space_has_nothing_recognized(empty_doc):
    sheet = empty_doc.layouts.new("Лист1")
    _square(sheet, 0, 0, 100, "Граница участка")
    _square(sheet, 10, 10, 10, "Здания")
    scene = parse_dxf_doc(empty_doc)
    assert scene["boundary"] is not None
    assert [o["type"] for o in scene["objects"]] == ["building"]
    assert any("листы" in w for w in scene["meta"]["warnings"])


def test_layer_report_lists_every_layer_with_what_was_taken(empty_doc):
    msp = empty_doc.modelspace()
    _square(msp, 0, 0, 100, "Граница участка")
    _square(msp, 10, 10, 10, "Здания")
    msp.add_line((0, 50), (100, 50), dxfattribs={"layer": "Тепловые сети"})
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "Слой 7"})
    msp.add_text("Дом 1", dxfattribs={"layer": "Подписи"})
    report = {r["name"]: r for r in DrawingView(msp, LayerResolver({"Здания": "building"})).layer_report({"Рельеф": 900})}
    assert report["Тепловые сети"] == {"name": "Тепловые сети", "entities": 1, "kind": "heat", "label": "Теплосеть", "source": "synonym"}
    assert report["Здания"]["source"] == "override"
    assert report["Слой 7"]["label"] is None and report["Слой 7"]["source"] is None
    assert report["Рельеф"]["entities"] == 900  # отброшен до разбора, но виден пользователю
    assert "Подписи" not in report  # тексты не считаются


def test_parsed_content_filter_keeps_what_the_parser_reads(empty_doc):
    msp = empty_doc.modelspace()
    plan = empty_doc.blocks.new("ПЛАН")
    _square(plan, 0, 0, 10, "Здания")
    title = empty_doc.blocks.new("ШТАМП")
    title.add_line((0, 0), (1, 0))
    kept = {
        "synonym": msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Тепловые сети"}),
        "text": msp.add_text("Дом", dxfattribs={"layer": "Рельеф"}),
        "block with plan": msp.add_blockref("ПЛАН", (0, 0), dxfattribs={"layer": "0"}),
        "user layer": msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Слой 7"}),
    }
    dropped = {
        "relief": msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "Рельеф"}),
        "title block": msp.add_blockref("ШТАМП", (0, 0), dxfattribs={"layer": "0"}),
    }
    keep = ParsedContentFilter(LayerResolver({"Слой 7": "path"}))
    assert {name for name, e in kept.items() if keep(e)} == set(kept)
    assert {name for name, e in dropped.items() if keep(e)} == set()
