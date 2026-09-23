"""violation_report.find_violations -- GreenPlan, Этап 6: список нарушений
норм по уже стоящим в сцене объектам (не через placement.Placer -- см.
докстринг модуля, задача другая: не "куда можно", а "что нарушает")."""

from helpers import make_object, make_scene, make_zone
from schemas import Point2
from violation_report import find_violations


def test_no_violations_on_empty_scene():
    assert find_violations(make_scene()) == []


def test_tree_too_close_to_building_is_a_violation():
    # Здание [-5,5]x[-5,5], для дерева норма -- 5 м от здания (SETBACK_NORMS).
    # Точка x=6 -- всего 1 м от края здания, меньше требуемых 5.
    zone = make_zone(type="building", min_distance=1.5)
    tree = make_object("t1", "tree", 6, 0)
    violations = find_violations(make_scene(restrictions=[zone], objects=[tree]))
    assert len(violations) == 1
    assert violations[0].object_id == "t1"
    assert violations[0].zone_id == zone.id
    assert violations[0].required_m == 5.0


def test_violation_carries_zone_severity():
    # Не все нарушения одинаково критичны -- warning отличим от forbidden.
    zone = make_zone(type="building", min_distance=1.5, severity="warning")
    tree = make_object("t1", "tree", 6, 0)
    violations = find_violations(make_scene(restrictions=[zone], objects=[tree]))
    assert violations[0].severity == "warning"


def test_tree_far_enough_from_building_is_not_a_violation():
    zone = make_zone(type="building", min_distance=1.5)
    tree = make_object("t1", "tree", 11, 0)  # 6 м от края -- норма 5 м пройдена
    assert find_violations(make_scene(restrictions=[zone], objects=[tree])) == []


def test_bush_uses_its_own_smaller_setback():
    # Кустарник у здания -- норма 1.5 м, не 5: точка в 2 м от края здания
    # нарушает для дерева, но не для куста.
    zone = make_zone(type="building", min_distance=1.5)
    bush = make_object("b1", "bush", 7, 0)
    assert find_violations(make_scene(restrictions=[zone], objects=[bush])) == []


def test_allowed_zone_never_produces_violations():
    zone = make_zone(type="protected_zone", severity="allowed", min_distance=0.0)
    tree = make_object("t1", "tree", 0, 0)
    assert find_violations(make_scene(restrictions=[zone], objects=[tree])) == []


def test_non_plant_objects_are_ignored():
    zone = make_zone(type="building", min_distance=1.5)
    lamp = make_object("l1", "lamp", 0, 0)
    assert find_violations(make_scene(restrictions=[zone], objects=[lamp])) == []


def test_generated_objects_are_checked_the_same_way_as_existing_ones():
    zone = make_zone(type="building", min_distance=1.5)
    tree = make_object("t1", "tree", 6, 0, metadata={"generated": True})
    violations = find_violations(make_scene(restrictions=[zone], objects=[tree]))
    assert len(violations) == 1


def test_multiple_objects_and_zones_find_exactly_the_real_violations():
    building = make_zone(id="b1", type="building", min_distance=1.5)
    road = make_zone(
        id="r1", type="road", min_distance=0.0,
        polygon=[Point2(x=20, z=-5), Point2(x=30, z=-5), Point2(x=30, z=5), Point2(x=20, z=5)],
    )
    close_to_building = make_object("t1", "tree", 6, 0)  # нарушает
    far_from_everything = make_object("t2", "tree", 100, 100)  # не нарушает
    close_to_road = make_object("t3", "tree", 21, 0)  # нарушает (road setback для tree = 2.0)

    scene = make_scene(restrictions=[building, road], objects=[close_to_building, far_from_everything, close_to_road])
    violations = {v.object_id for v in find_violations(scene)}
    assert violations == {"t1", "t3"}


def test_finds_all_zones_within_reach_not_just_the_nearest():
    # Регрессия на выбор STRtree.query() вместо .nearest() (см. докстринг
    # модуля про _build_zone_index): точка ровно между двумя зданиями,
    # в 1 м от края каждого -- норма для дерева 5 м, значит нарушает ОБЕ
    # зоны одновременно, а не только ближайшую из них.
    building_a = make_zone(id="a", type="building", min_distance=1.5)  # [-5,5]x[-5,5]
    building_b = make_zone(
        id="b", type="building", min_distance=1.5,
        polygon=[Point2(x=7, z=-5), Point2(x=17, z=-5), Point2(x=17, z=5), Point2(x=7, z=5)],
    )
    tree = make_object("t1", "tree", 6, 0)  # 1 м от края обоих зданий
    scene = make_scene(restrictions=[building_a, building_b], objects=[tree])
    zone_ids = {v.zone_id for v in find_violations(scene)}
    assert zone_ids == {"a", "b"}


def test_linden_within_10m_of_building_is_a_violation():
    # 743-ПП, табл. 3.6.1, прим. 3: широкая крона (липа, клён, дуб, каштан,
    # тополь) -- не ближе 10 м от здания. 7 м от стены -- берёзе можно, липе нет.
    zone = make_zone(type="building", min_distance=1.5)
    linden = make_object("t1", "tree", 12, 0, metadata={"species": "Липа мелколистная"})
    birch = make_object("t2", "tree", 0, 12, metadata={"species": "Берёза повислая"})
    violations = find_violations(make_scene(restrictions=[zone], objects=[linden, birch]))
    assert [v.object_id for v in violations] == ["t1"]
    assert violations[0].required_m == 10.0


def test_thorny_bush_near_path_is_a_violation():
    # СП 82.13330.2016, п. 9.22: колючие -- не ближе 2 м от пешеходных коммуникаций.
    path = make_zone(type="pedestrian_path", name="PATH_1", severity="warning", min_distance=0.5)
    rose = make_object("b1", "bush", 6, 0, metadata={"species": "Роза морщинистая"})
    spirea = make_object("b2", "bush", 0, 6, metadata={"species": "Спирея серая"})
    violations = find_violations(make_scene(restrictions=[path], objects=[rose, spirea]))
    assert [v.object_id for v in violations] == ["b1"]
    assert violations[0].required_m == 2.0
