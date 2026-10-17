"""dxf_parsing/networks.py и коридоры сетей в zones.py: диаметры из подписей,
выноски, пунктир."""

import pytest

from dxf_parsing import networks
from dxf_parsing.networks import Annotation, Label, LabelIndex, outer_width_m, split_leaders, trace_runs
from parse_dxf import Transform, extract_restrictions

# --- подписи --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,width",
    [
        ("d=200ст.", 0.2),
        ("d=219н.д.ст.Б.Д.", 0.219),
        ("d = 500 ГНБ стр.", 0.5),
        ("d=2х150ст.Б.Д.", 0.3),  # две трубы, «х» русская
        ("d=2x500б.к.", 1.0),
        ("ф-р d=900ст.", 0.9),  # футляр -- по СП 42 отступ от него
        ("щит d=2560", 2.56),
        ("d=2x325 2150x1260", 2.15),  # канал теплосети главнее труб в нём
        ("2100х1100", 2.1),
        ("Ø300", 0.3),
    ],
)
def test_outer_width_from_survey_labels(text, width):
    assert outer_width_m(text) == pytest.approx(width)


@pytest.mark.parametrize("text", ["4тр.", "1к.Д.С.", "МТС", "обр.", "Б.Д.", "d=5", "d=99999", "Кабель 10кВ"])
def test_labels_without_a_diameter(text):
    assert outer_width_m(text) is None


# --- выноски --------------------------------------------------------------------

DASHES = [[(x * 1.5, 0.0), (x * 1.5 + 1.0, 0.0)] for x in range(20)]  # штрих 1 м, промежуток 0,5 м


def _labels(*labels):
    return LabelIndex([Label(text, x, y, h) for text, x, y, h in labels])


def test_leader_with_a_label_on_its_shelf_is_not_a_pipe():
    # Кончик на трубе в (10, 0), колено (14, 6), полка вправо под подписью.
    text = "d=200ст."
    shelf = len(text) * 1.0 * 0.8 * 0.9
    leader = [(10.25, 0.0), (14.0, 6.0), (14.0 + shelf, 6.0)]
    kept, notes = split_leaders([*DASHES, leader], _labels((text, 14.2, 6.3, 1.0)), 1.0)
    assert leader not in kept and len(kept) == len(DASHES)
    assert notes == [Annotation(text, 10.25, 0.0, True, 1.0)]


def test_dash_next_to_a_label_along_the_pipe_stays_a_pipe():
    # Подпись вдоль трубы над штрихом: штрих продолжает соседние по прямой --
    # это не выноска, хотя длина штриха похожа на ширину подписи.
    text = "1к."
    kept, notes = split_leaders(DASHES, _labels((text, 6.0, 0.4, 1.0)), 1.0)
    assert kept == DASHES
    assert notes == [Annotation(text, 6.0, 0.4, False, 1.0)]


def test_long_polyline_is_never_a_leader():
    pipe = [(0.0, 0.0), (40.0, 0.0), (80.0, 0.0)]
    kept, _notes = split_leaders([pipe], _labels(("d=200", 70.0, 0.3, 1.0)), 1.0)
    assert kept == [pipe]


# --- пунктир --------------------------------------------------------------------


def test_dashes_in_line_are_one_run_and_a_branch_is_another():
    branch = [[(15.0, 0.5 + y * 1.5), (15.0, 1.5 + y * 1.5)] for y in range(5)]  # отвод под прямым углом
    runs = trace_runs([*DASHES, *branch], 0.8)
    assert len(set(runs[: len(DASHES)])) == 1
    assert len(set(runs[len(DASHES):])) == 1
    assert runs[0] != runs[-1]


def test_parallel_pipes_are_different_runs():
    upper = [[(x * 1.5, 0.6), (x * 1.5 + 1.0, 0.6)] for x in range(20)]
    runs = trace_runs([*DASHES, *upper], 0.8)
    assert runs[0] != runs[-1]


# --- коридоры -------------------------------------------------------------------


def _gas_tile(doc, labelled=True):
    msp = doc.modelspace()
    for (x0, y0), (x1, y1) in DASHES:
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "Газопровод"})
    if labelled:
        text = "d=200ст."
        shelf = len(text) * 0.8 * 0.9
        msp.add_lwpolyline([(10.25, 0.0), (14.0, 6.0), (14.0 + shelf, 6.0)], dxfattribs={"layer": "Газопровод"})
        msp.add_text(text, dxfattribs={"layer": "Газопровод", "height": 1.0, "insert": (14.2, 6.3)})
    return msp


def test_pipe_with_a_known_diameter_is_a_zone_as_wide_as_the_pipe(empty_doc):
    zones = [z for z in extract_restrictions(_gas_tile(empty_doc), Transform()) if z["type"] == "gas_pipeline"]
    assert len(zones) == 1  # штрихи пунктира -- одна зона, выноска -- не зона
    xs = [p["z"] for p in zones[0]["polygon"]]
    assert max(xs) - min(xs) == pytest.approx(0.2, abs=0.02)
    assert zones[0]["minDistance"] == pytest.approx(0.1)
    assert "наружной стенки" in zones[0]["message"] and "d=200" in zones[0]["message"]


def test_pipe_without_a_diameter_keeps_the_old_corridor(empty_doc):
    zones = [z for z in extract_restrictions(_gas_tile(empty_doc, labelled=False), Transform()) if z["type"] == "gas_pipeline"]
    widths = {round(max(p["z"] for p in z["polygon"]) - min(p["z"] for p in z["polygon"]), 3) for z in zones}
    assert widths == {4.0}  # 2 x minDistance газопровода, как раньше


def test_diameter_rules_do_not_apply_to_cables(empty_doc):
    msp = empty_doc.modelspace()
    for (x0, y0), (x1, y1) in DASHES:
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "Кабель электрический"})
    msp.add_text("d=100", dxfattribs={"layer": "Кабель электрический", "height": 1.0, "insert": (6.0, 0.4)})
    zones = [z for z in extract_restrictions(msp, Transform()) if z["type"] == "electrical"]
    assert all("наружной стенки" not in z["message"] for z in zones)


def test_network_types_with_diameters():
    assert set(networks.NETWORK_TYPES) == {"gas_pipeline", "sewer", "water_pipeline", "heat_network"}


# --- воздушные ЛЭП: класс напряжения из имени слоя (issue #107) ---------------------


@pytest.mark.parametrize(
    "layer,kv",
    [("ВЛ 10кВ", 10.0), ("ЛЭП 0,4 кВ", 0.4), ("OHL_110kV", 110.0), ("ВЛ-35 кВ", 35.0), ("ЛЭП", None), ("ВЛ 10 кВт", None)],
)
def test_overhead_line_voltage_from_layer_name(layer, kv):
    from dxf_parsing.zones import overhead_voltage_kv

    assert overhead_voltage_kv(layer) == kv


def test_overhead_line_zone_carries_the_voltage(empty_doc):
    msp = empty_doc.modelspace()
    msp.add_line((0, 0), (50, 0), dxfattribs={"layer": "ЛЭП 0,4 кВ"})
    zones = [z for z in extract_restrictions(msp, Transform()) if z["type"] == "overhead_power_line"]
    assert zones and all(z["voltageKv"] == 0.4 for z in zones)
