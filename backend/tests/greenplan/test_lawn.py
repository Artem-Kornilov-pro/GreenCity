"""greenplan/lawn.py -- газон GreenPlan: площадь, а не объекты."""

import pytest

from greenplan.lawn import DEFAULT_SHRUB_RADIUS_M, LAWN_KIND, MIN_LAWN_AREA_SQM, existing_lawns, lawn_totals, plan_lawns
from helpers import make_boundary, make_object, make_scene, make_zone, rect_points

SITE_AREA = 100 * 100  # make_boundary() по умолчанию: [-50,50]x[-50,50]


def _new_area(scene) -> float:
    return lawn_totals(plan_lawns(scene, {}))[0]


def test_empty_site_is_all_new_lawn():
    lawns = plan_lawns(make_scene(), {})
    assert [a.status for a in lawns] == ["new"]
    assert lawns[0].kind == LAWN_KIND
    assert lawns[0].id == "lawn_new_001"
    assert lawns[0].area_sqm == pytest.approx(SITE_AREA)


def test_no_boundary_no_lawn():
    assert plan_lawns(make_scene(boundary=None), {}) == []


@pytest.mark.parametrize("zone_type", ["building", "road", "pedestrian_path", "playground_zone", "custom", "transformer"])
def test_hard_surfaces_are_cut_out(zone_type):
    scene = make_scene(restrictions=[make_zone(type=zone_type)])  # 10x10 м
    assert _new_area(scene) == pytest.approx(SITE_AREA - 100, abs=1)


@pytest.mark.parametrize("zone_type", ["gas_pipeline", "sewer", "water_pipeline", "electrical", "signal_cable", "heat_network"])
def test_lawn_is_allowed_over_utility_networks(zone_type):
    # Таблица 9.1 СП 42 нормирует расстояния только для деревьев и кустарников.
    scene = make_scene(restrictions=[make_zone(type=zone_type, severity="warning")])
    assert _new_area(scene) == pytest.approx(SITE_AREA)


def test_forbidden_protected_zone_is_cut_out():
    forbidden = make_scene(restrictions=[make_zone(type="protected_zone", severity="forbidden")])
    assert _new_area(forbidden) == pytest.approx(SITE_AREA - 100, abs=1)


def test_selection_zone_does_not_limit_lawn():
    selection = make_zone(type="selection", name="SELECTION", severity="allowed", polygon=rect_points(0, 0, 10, 10))
    assert _new_area(make_scene(restrictions=[selection])) == pytest.approx(SITE_AREA)


def test_shrubs_become_beds_and_a_row_merges_into_one_bed():
    # Ряд кустов с шагом 1.4 м -- одна сплошная клумба, а не кружки.
    row = [make_object(f"b{i}", "bush", -20 + 1.4 * i, 0) for i in range(10)]
    lawns = plan_lawns(make_scene(objects=row), {})
    [lawn] = lawns
    assert len(lawn.holes) == 1
    circles = 10 * 3.14159 * DEFAULT_SHRUB_RADIUS_M**2
    assert SITE_AREA - lawn.area_sqm > circles  # промежутки между кустами тоже клумба


def test_trees_stand_in_the_lawn():
    trees = [make_object(f"t{i}", "tree", i * 10 - 20, 0) for i in range(5)]
    assert _new_area(make_scene(objects=trees)) == pytest.approx(SITE_AREA)


def test_narrow_slivers_are_dropped():
    # Две дорожки с зазором 0.3 м: полосу уже MIN_LAWN_WIDTH_M газоном не считаем.
    left = make_zone(id="p1", type="pedestrian_path", polygon=rect_points(-50, -2, 0, 2))
    right = make_zone(id="p2", type="pedestrian_path", polygon=rect_points(0.3, -2, 50, 2))
    area = _new_area(make_scene(restrictions=[left, right]))
    assert area == pytest.approx(SITE_AREA - 400, abs=1.5)


def test_tiny_pieces_are_dropped():
    # Уголок 2x2 м между зданием и границей -- не газон.
    building = make_zone(polygon=rect_points(-50, -48, 50, 50))
    corner_block = make_zone(id="z2", polygon=rect_points(-48, -50, 50, -48))
    lawns = plan_lawns(make_scene(restrictions=[building, corner_block]), {})
    assert all(a.area_sqm >= MIN_LAWN_AREA_SQM for a in lawns)
    assert lawns == []


def test_existing_grass_cover_is_kept_and_ground_is_new():
    grass = make_zone(id="g", type="lawn", name="GRASS", severity="allowed", polygon=rect_points(-50, -50, 0, 50))
    ground = make_zone(id="o", type="lawn", name="GROUND", severity="allowed", polygon=rect_points(0, -50, 50, 50))
    lawns = plan_lawns(make_scene(restrictions=[grass, ground]), {})
    assert lawn_totals(lawns) == pytest.approx((5000, 5000))
    assert {a.id for a in lawns} == {"lawn_new_001", "lawn_existing_001"}


def test_allowed_zones_limit_the_lawn():
    # Разрешённые зоны есть -- газон только в них, а не на всём участке.
    ground = make_zone(id="o", type="lawn", name="GROUND", severity="allowed", polygon=rect_points(0, 0, 20, 20))
    assert _new_area(make_scene(restrictions=[ground])) == pytest.approx(400)


def test_lawn_is_clipped_to_boundary():
    big = make_zone(id="o", type="lawn", name="GROUND", severity="allowed", polygon=rect_points(-100, -100, 100, 100))
    scene = make_scene(boundary=make_boundary(0, 0, 30, 30), restrictions=[big])
    assert _new_area(scene) == pytest.approx(900)


def test_existing_lawns_are_only_the_drawing_lawn_cover():
    # Сразу после загрузки чертежа: газон со слоёв газона -- сохраняемый,
    # открытая земля рядом газоном не считается (его предлагает GreenPlan).
    grass = make_zone(id="g", type="lawn", name="ДВ_ГП_П_Газон", severity="allowed", polygon=rect_points(-50, -50, 0, 50))
    ground = make_zone(id="o", type="lawn", name="__computed_ground__", severity="allowed", polygon=rect_points(0, -50, 50, 50))
    lawns = existing_lawns(make_scene(restrictions=[grass, ground]), {})
    assert [a.status for a in lawns] == ["existing"]
    assert lawns[0].area_sqm == pytest.approx(SITE_AREA / 2)


def test_no_lawn_cover_in_drawing_no_existing_lawn():
    assert existing_lawns(make_scene(), {}) == []
