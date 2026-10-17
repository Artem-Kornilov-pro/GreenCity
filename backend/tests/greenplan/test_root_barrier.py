"""Посадка «условно, с корнезащитой» (issue #106): СП 42.13330.2016, прим. 4-7
к табл. 9.1 -- дерево ближе нормы к подземной сети с прикорневым барьером."""

import pytest
from shapely.geometry import Point

from core.setback_norms import ROOT_BARRIER_MIN_M, ROOT_BARRIER_SOURCE, setback_basis, setback_for
from core.shapes import polygon_from_points
from greenplan.explanations import explain_plants
from greenplan.options import GreenPlanOptions
from greenplan.pipeline import run_greenplan
from greenplan.violation_report import find_violations
from helpers import make_scene, make_zone, rect_points

# --- нормы ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "zone_type,full",
    [("gas_pipeline", 1.5), ("sewer", 1.5), ("water_pipeline", 2.0), ("heat_network", 2.0), ("electrical", 2.0), ("signal_cable", 2.0)],
)
def test_tree_next_to_an_underground_network_needs_less_with_a_root_barrier(zone_type, full):
    assert setback_for(zone_type, "tree", 0.1) == full
    assert setback_for(zone_type, "tree", 0.1, root_barrier=True) == ROOT_BARRIER_MIN_M
    assert setback_basis(zone_type, "tree", 0.1, root_barrier=True) == (ROOT_BARRIER_MIN_M, ROOT_BARRIER_SOURCE)


@pytest.mark.parametrize("zone_type", ["building", "road", "pedestrian_path"])
def test_root_barrier_does_not_touch_buildings_and_roads(zone_type):
    assert setback_for(zone_type, "tree", 0.1, root_barrier=True) == setback_for(zone_type, "tree", 0.1)


def test_root_barrier_is_for_trees_only():
    assert setback_for("heat_network", "bush", 0.1, root_barrier=True) == setback_for("heat_network", "bush", 0.1)


def test_species_rule_still_tightens_the_setback():
    # Тополь -- 3 м от канализации (экспертная оценка): барьер его не отменяет.
    assert setback_for("sewer", "tree", 0.1, species="Тополь берлинский", root_barrier=True) == 3.0


# --- GreenPlan --------------------------------------------------------------------


def _site_with_pipes():
    """Двор 100 x 100 м, поперёк -- водопровод и газопровод с известным
    диаметром (зона -- сама труба)."""
    water = make_zone("w1", "water_pipeline", "Водопровод", min_distance=0.15, polygon=rect_points(-50, -0.15, 50, 0.15))
    gas = make_zone("g1", "gas_pipeline", "Газопровод", min_distance=0.1, polygon=rect_points(-0.1, -50, 0.1, 50))
    scene = make_scene(restrictions=[water, gas])
    scene.meta.origin = {"x": 0.0, "y": 0.0}  # как у сцены из чертежа -- для координат в объяснениях
    return scene


def test_greenplan_without_the_option_marks_nothing():
    run = run_greenplan(_site_with_pipes(), GreenPlanOptions())
    assert not any(o.metadata.get("rootBarrier") for o in run.new_plants)


def test_greenplan_with_root_barrier_plants_closer_and_marks_those_trees():
    scene = _site_with_pipes()
    plain = run_greenplan(scene, GreenPlanOptions())
    run = run_greenplan(scene, GreenPlanOptions(root_barrier=True))

    conditional = [o for o in run.new_plants if o.metadata.get("rootBarrier")]
    assert conditional, "у труб должны появиться деревья с корнезащитой"
    assert sum(o.type == "tree" for o in run.new_plants) >= sum(o.type == "tree" for o in plain.new_plants)
    pipes = {z.id: polygon_from_points(z.polygon) for z in scene.restrictions}
    for tree in conditional:
        point = Point(tree.position.x, tree.position.z)
        nearest = min(geom.distance(point) for geom in pipes.values())
        assert nearest >= ROOT_BARRIER_MIN_M - 1e-6  # не ближе барьерного минимума
        assert set(tree.metadata["rootBarrier"]) <= {"водопровод", "газопровод"}
    # Отчёт о нарушениях учитывает барьер: у новых посадок нарушений нет.
    new_ids = {o.id for o in run.new_plants}
    assert [v for v in find_violations(run.scene) if v.object_id in new_ids] == []
    assert any("корнезащит" in note for note in run.notes)
    statuses = {e.id: e.status for e in explain_plants(run.scene, run.assignments)}
    assert all(statuses[o.id] == "условно, с корнезащитой" for o in conditional)


def test_tree_with_a_barrier_moved_too_close_is_still_a_violation():
    scene = _site_with_pipes()
    run = run_greenplan(scene, GreenPlanOptions(root_barrier=True))
    tree = next(o for o in run.new_plants if o.metadata.get("rootBarrier"))
    tree.position.z = 0.5  # 0,35 м от стенки водопровода -- меньше барьерного метра
    tree.position.x = 20.0
    assert any(v.object_id == tree.id for v in find_violations(run.scene))
