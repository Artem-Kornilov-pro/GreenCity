"""Воздушные ЛЭП (issue #107): отступ дерева до кроны по ПУЭ (п. 2.4.8,
табл. 2.5.21), класс напряжения -- из имени слоя или параметров GreenPlan."""

import pytest

from core.setback_norms import CROWN_RADIUS_M, overhead_line_setback, pue_crown_distance, setback_basis, setback_for
from greenplan.options import GreenPlanOptions
from greenplan.pipeline import run_greenplan
from greenplan.violation_report import find_violations
from helpers import make_scene, make_zone, rect_points


@pytest.mark.parametrize(
    "kv,crown",
    [(0.4, 1.0), (1.0, 1.0), (6, 3.0), (10, 3.0), (20, 3.0), (35, 4.0), (110, 4.0), (220, 5.0), (330, 6.0), (500, 7.0), (750, 7.0)],
)
def test_pue_distance_to_crown_by_voltage(kv, crown):
    assert pue_crown_distance(kv)[0] == crown


def test_unknown_voltage_is_taken_as_10_kv():
    assert pue_crown_distance(None) == pue_crown_distance(10)


def test_tree_setback_from_the_corridor_edge_counts_crown_and_corridor():
    # Коридор зоны -- 2 м от провода; до кроны 3 м + крона 2,5 м = 5,5 м от провода.
    assert overhead_line_setback(2.0, 10) == pytest.approx(3.0 + CROWN_RADIUS_M - 2.0)
    assert setback_for("overhead_power_line", "tree", 2.0, voltage_kv=10) == pytest.approx(3.5)
    assert setback_for("overhead_power_line", "tree", 2.0, voltage_kv=0.4) == pytest.approx(1.5)
    assert setback_for("overhead_power_line", "tree", 2.0, voltage_kv=110) == pytest.approx(4.5)


def test_bushes_near_overhead_lines_are_unchanged():
    assert setback_for("overhead_power_line", "bush", 2.0, voltage_kv=110) == 2.0


def test_norm_reference_names_pue_and_the_assumed_voltage():
    _d, source = setback_basis("overhead_power_line", "tree", 2.0)
    assert "ПУЭ" in source and "принято 10 кВ" in source
    _d, source = setback_basis("overhead_power_line", "tree", 2.0, voltage_kv=0.4)
    assert "п. 2.4.8" in source and "0.4 кВ" in source


def _site_with_line(voltage=None):
    line = make_zone("ohl1", "overhead_power_line", "ВЛ", severity="warning", min_distance=2.0, polygon=rect_points(-50, -2, 50, 2))
    if voltage is not None:
        line = line.model_copy(update={"voltageKv": voltage})
    return make_scene(restrictions=[line])


def _closest_tree(run):
    return min(abs(o.position.z) for o in run.new_plants if o.type == "tree")


def test_greenplan_keeps_trees_crown_away_from_the_wire():
    run = run_greenplan(_site_with_line())
    # Провод -- по оси z = 0, коридор зоны 2 м: ствол не ближе 5,5 м к проводу.
    assert _closest_tree(run) >= 2.0 + 3.5 - 1e-6
    new = {o.id for o in run.new_plants}
    assert [v for v in find_violations(run.scene) if v.object_id in new] == []


def test_voltage_option_applies_to_lines_without_a_voltage():
    run = run_greenplan(_site_with_line(), GreenPlanOptions(overhead_voltage_kv=0.4))
    assert {z.voltageKv for z in run.scene.restrictions if z.type == "overhead_power_line"} == {0.4}
    assert _closest_tree(run) >= 2.0 + 1.5 - 1e-6
    # Класс напряжения из имени слоя главнее параметра.
    run = run_greenplan(_site_with_line(voltage=110), GreenPlanOptions(overhead_voltage_kv=0.4))
    assert {z.voltageKv for z in run.scene.restrictions if z.type == "overhead_power_line"} == {110}
