"""placement.py -- геометрический планировщик правок текстом. Сцены строятся
руками (helpers.py) для точного контроля над геометрией: участок 100x100 м
([-50,-50]..[50,50]), если не сказано иное."""

import math

import placement
import pytest
from helpers import make_boundary, make_object, make_scene, make_zone
from placement import (
    OBJECT_CLEARANCE_M,
    Placer,
    PointIndex,
    _geometry,
    _segment_visible,
    clamp,
    pick_near,
    pick_spread,
    polygons,
    shortest_path,
    spread_subset,
)
from schemas import Point2
from shapely.geometry import MultiPolygon, Point, Polygon

# --- clamp / _geometry / polygons ------------------------------------------


def test_clamp_bounds_value_both_sides():
    assert clamp(5, 0, 10) == 5
    assert clamp(-5, 0, 10) == 0
    assert clamp(15, 0, 10) == 10


def test_geometry_returns_none_for_fewer_than_three_points():
    assert _geometry([Point2(x=0, z=0), Point2(x=1, z=1)]) is None


def test_geometry_repairs_self_intersecting_polygon_via_buffer_zero():
    # Полигон-бабочка: самопересекающийся четырёхугольник.
    pts = [Point2(x=0, z=0), Point2(x=10, z=10), Point2(x=10, z=0), Point2(x=0, z=10)]
    geom = _geometry(pts)
    assert geom is not None
    assert geom.is_valid


def test_geometry_single_true_picks_largest_polygon_from_multipolygon():
    # Настоящая "восьмёрка" из двух треугольных долей, соединённых точкой --
    # buffer(0) честно распадается на MultiPolygon, а не на один Polygon
    # (в отличие от простого "бабочка"-четырёхугольника выше).
    pts = [
        Point2(x=0, z=0), Point2(x=5, z=5), Point2(x=10, z=0),
        Point2(x=10, z=10), Point2(x=5, z=5), Point2(x=0, z=10),
    ]
    geom = _geometry(pts, single=True)
    assert geom.geom_type == "Polygon"
    assert geom.area == pytest.approx(25.0)  # больший из двух треугольников (площадь 25 каждый -- выбор детерминирован по первому)


def test_geometry_returns_none_for_zero_area_degenerate_polygon():
    # Три коллинеарные точки -- формально "полигон" из 3 точек, но нулевой площади.
    pts = [Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=10, z=0)]
    assert _geometry(pts) is None


def test_polygons_handles_none_polygon_and_multipolygon():
    assert polygons(None) == []
    p = Polygon([(0, 0), (1, 0), (1, 1)])
    assert polygons(p) == [p]
    mp = MultiPolygon([p, Polygon([(5, 5), (6, 5), (6, 6)])])
    assert len(polygons(mp)) == 2


# --- PointIndex --------------------------------------------------------------


def test_point_index_within_finds_nearby_points():
    idx = PointIndex(cell=2.0)
    idx.add("a", 0, 0, "tree")
    idx.add("b", 100, 100, "tree")
    hits = list(idx.within(0, 0, radius=5))
    assert [h[0] for h in hits] == ["a"]


def test_point_index_has_within_false_when_nothing_close():
    idx = PointIndex(cell=2.0)
    idx.add("a", 0, 0, "tree")
    assert idx.has_within(50, 50, radius=5) is False


def test_point_index_remove_makes_point_disappear():
    idx = PointIndex(cell=2.0)
    idx.add("a", 0, 0, "tree")
    idx.remove("a")
    assert idx.has_within(0, 0, radius=5) is False


def test_point_index_add_twice_moves_the_point_not_duplicates():
    idx = PointIndex(cell=2.0)
    idx.add("a", 0, 0, "tree")
    idx.add("a", 100, 100, "tree")
    assert idx.has_within(0, 0, radius=1) is False
    assert idx.has_within(100, 100, radius=1) is True


# --- Placer: конструктор / region / required --------------------------------


def test_placer_site_falls_back_to_zones_envelope_without_boundary():
    zone = make_zone(polygon=[Point2(x=0, z=0), Point2(x=10, z=0), Point2(x=10, z=10)])
    scene = make_scene(boundary=None, restrictions=[zone])
    placer = Placer(scene)
    assert placer.site is not None


def test_placer_site_is_none_without_boundary_or_zones():
    scene = make_scene(boundary=None, restrictions=[])
    placer = Placer(scene)
    assert placer.site is None


def test_placer_required_tree_uses_setback_norms():
    placer = Placer(make_scene())
    zone = make_zone(type="building")
    assert placer.required(zone, "tree") == 5.0
    assert placer.required(zone, "bush") == 1.5


def test_placer_required_none_kind_uses_furniture_clearance():
    placer = Placer(make_scene())
    zone = make_zone(type="building")
    assert placer.required(zone, None) == placement.FURNITURE_CLEARANCE_M


def test_placer_region_excludes_forbidden_and_warning_but_not_allowed():
    forbidden = make_zone(id="f1", type="building", severity="forbidden", polygon=[
        Point2(x=-5, z=-5), Point2(x=5, z=-5), Point2(x=5, z=5), Point2(x=-5, z=5)
    ])
    allowed = make_zone(id="a1", type="protected_zone", severity="allowed", min_distance=0.0, polygon=[
        Point2(x=20, z=20), Point2(x=30, z=20), Point2(x=30, z=30), Point2(x=20, z=30)
    ])
    scene = make_scene(restrictions=[forbidden, allowed])
    placer = Placer(scene)
    # Внутри "allowed" зоны сажать можно -- она не сужает область.
    assert placer.in_region(25, 25, "tree") is True
    # А внутри "forbidden" -- нельзя.
    assert placer.in_region(0, 0, "tree") is False


def test_placer_region_caches_per_kind(monkeypatch):
    placer = Placer(make_scene())
    first = placer.region("tree")
    second = placer.region("tree")
    assert first is second  # тот же закэшированный кортеж, не пересчитан


def test_placer_region_is_none_when_area_fully_consumed():
    # Здание размером почти со весь допустимый (после SITE_CLEARANCE_M) участок.
    huge_building = make_zone(
        type="building", severity="forbidden", min_distance=0.0,
        polygon=[Point2(x=-49, z=-49), Point2(x=49, z=-49), Point2(x=49, z=49), Point2(x=-49, z=49)],
    )
    placer = Placer(make_scene(restrictions=[huge_building]))
    area, prepared = placer.region("tree")
    assert area is None
    assert prepared is None


# --- Placer.blocker / is_free -------------------------------------------------


def test_blocker_enforces_lamp_to_tree_norm_of_4m():
    lamp = make_object("lamp_1", "lamp", 0, 0)
    placer = Placer(make_scene(objects=[lamp]))
    hit = placer.blocker(3.9, 0, "tree", obj_type="tree")
    assert hit is not None
    hit_far = placer.blocker(4.1, 0, "tree", obj_type="tree")
    assert hit_far is None


def test_blocker_norm_applies_in_both_directions():
    """Дерево нельзя ставить в 4 м от фонаря, и фонарь нельзя ставить в 4 м
    от уже существующего дерева -- норма симметрична (см. docstring blocker)."""
    tree = make_object("tree_1", "tree", 0, 0)
    placer = Placer(make_scene(objects=[tree]))
    hit = placer.blocker(3.9, 0, None, obj_type="lamp")
    assert hit is not None


def test_blocker_tree_to_tree_minimum_distance():
    tree = make_object("tree_1", "tree", 0, 0)
    placer = Placer(make_scene(objects=[tree]))
    assert placer.blocker(2.9, 0, "tree", obj_type="tree") is not None
    assert placer.blocker(3.1, 0, "tree", obj_type="tree") is None


def test_blocker_default_object_clearance_applies_without_specific_norm():
    bench = make_object("bench_1", "bench", 0, 0)
    placer = Placer(make_scene(objects=[bench]))
    assert placer.blocker(OBJECT_CLEARANCE_M - 0.1, 0, None) is not None
    assert placer.blocker(OBJECT_CLEARANCE_M + 0.1, 0, None) is None


def test_is_free_false_outside_region_even_if_no_object_nearby():
    building = make_zone(type="building", severity="forbidden")
    placer = Placer(make_scene(restrictions=[building]))
    assert placer.is_free(0, 0, "tree") is False


def test_is_free_true_far_from_everything():
    placer = Placer(make_scene())
    assert placer.is_free(20, 20, "tree") is True


# --- Placer.explain ------------------------------------------------------------


def test_explain_outside_site_boundary():
    placer = Placer(make_scene())
    assert placer.explain(1000, 1000, "tree") == "за границей участка"


def test_explain_too_close_to_site_boundary():
    placer = Placer(make_scene())
    assert "границе участка" in placer.explain(49.99, 0, "tree")


def test_explain_inside_forbidden_zone_message():
    zone = make_zone(type="building", message="Отступ от здания")
    placer = Placer(make_scene(restrictions=[zone]))
    assert "Отступ от здания" in placer.explain(0, 0, "tree")


def test_explain_too_close_to_zone_but_outside_it():
    zone = make_zone(type="building", message="Отступ от здания")
    placer = Placer(make_scene(restrictions=[zone]))
    # Чуть за пределами полигона [-5,5]x[-5,5], но ближе положенных 5 м для дерева
    msg = placer.explain(6.0, 0, "tree")
    assert "Отступ от здания" in msg
    assert "нужно 5.0" in msg


def test_explain_blocked_by_nearby_object():
    lamp = make_object("lamp_1", "lamp", 20, 20)
    placer = Placer(make_scene(objects=[lamp]))
    msg = placer.explain(21, 20, "tree")
    assert "lamp_1" in msg


def test_explain_allowed_zone_does_not_block():
    allowed = make_zone(type="protected_zone", severity="allowed", min_distance=0.0)
    placer = Placer(make_scene(restrictions=[allowed]))
    assert placer.explain(0, 0, "tree") == "у самой границы зоны ограничений"


# --- Placer.nearest_free -------------------------------------------------------


def test_nearest_free_returns_same_point_when_already_free():
    placer = Placer(make_scene())
    assert placer.nearest_free(20, 20, "tree") == (20, 20)


def test_nearest_free_snaps_just_outside_a_forbidden_zone():
    zone = make_zone(type="building")  # [-5,5]x[-5,5], дерево -- отступ 5 м
    placer = Placer(make_scene(restrictions=[zone]))
    # (7, 0) внутри требуемого отступа (нужно 5 м от здания, край здания в
    # x=5, значит нужно x>=10) -- но снаружи самого здания и в пределах
    # MAX_SNAP_DISTANCE_M от годного места.
    result = placer.nearest_free(7, 0, "tree")
    assert result is not None
    assert placer.is_free(*result, "tree") is True


def test_nearest_free_falls_back_to_ring_search_when_exact_snap_is_blocked():
    zone = make_zone(type="building")  # [-5,5]x[-5,5]
    # Ровно там, где лёг бы точный ближайший снап от (7,0) -- (10.1, 0), см.
    # nearest_points(area) -- ставим фонарь: прямой снап заблокирован, должен
    # сработать перебор колец вокруг точки.
    lamp = make_object("lamp_block", "lamp", 10.15, 0.0)
    placer = Placer(make_scene(restrictions=[zone], objects=[lamp]))
    result = placer.nearest_free(7, 0, "tree")
    assert result is not None
    assert placer.is_free(*result, "tree") is True
    # Не тот самый заблокированный снап -- значит, действительно сработал
    # перебор колец, а не "повезло, что было свободно".
    assert math.hypot(result[0] - 10.15, result[1] - 0.0) > 0.5


def test_nearest_free_exhausts_ring_search_and_returns_none():
    # Здание закрывает середину участка так, что до годного места (у самого
    # края 100x100 двора) дальше MAX_SNAP_DISTANCE_M -- и точный снап, и
    # полный перебор колец вокруг (0,0) должны провалиться, но area при этом
    # НЕ None (свободно у краёв) -- в отличие от "нигде вообще нет места" ниже.
    big_building = make_zone(
        type="building", severity="forbidden", min_distance=0.0,
        polygon=[Point2(x=-30, z=-30), Point2(x=30, z=-30), Point2(x=30, z=30), Point2(x=-30, z=30)],
    )
    placer = Placer(make_scene(restrictions=[big_building]))
    assert placer.region("tree")[0] is not None  # где-то у краёв места есть
    assert placer.nearest_free(0, 0, "tree") is None


def test_nearest_free_returns_none_when_nothing_within_reach():
    # Точка внутри огромного здания, занимающего почти весь участок -- регион
    # для дерева пуст (area is None), значит и снап невозможен.
    huge_building = make_zone(
        type="building", severity="forbidden", min_distance=0.0,
        polygon=[Point2(x=-49, z=-49), Point2(x=49, z=-49), Point2(x=49, z=49), Point2(x=-49, z=49)],
    )
    placer = Placer(make_scene(restrictions=[huge_building]))
    assert placer.nearest_free(0, 0, "tree") is None


# --- Placer.occupy / release / add_zone ---------------------------------------


def test_occupy_then_blocker_sees_the_new_object():
    placer = Placer(make_scene())
    assert placer.blocker(0, 0, None) is None
    placer.occupy("new_1", 0, 0, "bench")
    assert placer.blocker(0.1, 0.1, None) is not None


def test_release_removes_the_object_from_the_index():
    placer = Placer(make_scene())
    placer.occupy("new_1", 0, 0, "bench")
    placer.release("new_1")
    assert placer.blocker(0, 0, None) is None


def test_add_zone_invalidates_region_and_target_caches():
    placer = Placer(make_scene())
    assert placer.is_free(0, 0, "tree") is True  # прогреваем кэш region("tree")
    new_zone = make_zone(id="z2", type="building", polygon=[
        Point2(x=-5, z=-5), Point2(x=5, z=-5), Point2(x=5, z=5), Point2(x=-5, z=5)
    ])
    placer.add_zone(new_zone)
    assert placer.is_free(0, 0, "tree") is False  # кэш сброшен, новая зона учтена


def test_add_zone_ignores_degenerate_polygon():
    placer = Placer(make_scene())
    from schemas import Point2

    degenerate = make_zone(polygon=[Point2(x=0, z=0), Point2(x=1, z=1)])
    placer.add_zone(degenerate)
    assert len(placer.zones) == 0


# --- Placer._target_zones / target_geometry / target_summary / target_distance -


def test_target_zones_parking_matched_by_layer_name_not_type():
    parking = make_zone(type="custom", name="PARKING_1", severity="warning", min_distance=1.0)
    placer = Placer(make_scene(restrictions=[parking]))
    assert placer.target_geometry("parking") is not None


def test_target_zones_playground_maps_to_playground_zone_type():
    zone = make_zone(type="playground_zone", name="PLAYGROUND_1", severity="warning", min_distance=1.0)
    placer = Placer(make_scene(restrictions=[zone]))
    assert placer.target_geometry("playground") is not None


def test_target_zones_fixed_target_with_no_matches_returns_empty_not_custom():
    placer = Placer(make_scene())
    assert placer.target_geometry("building") is None


def test_target_zones_custom_named_zone_matched_case_insensitively():
    from schemas import Point2

    zone = make_zone(id="custom1", type="forbidden", name="Детская зона", severity="forbidden", min_distance=0.0, polygon=[
        Point2(x=10, z=10), Point2(x=20, z=10), Point2(x=20, z=20)
    ])
    placer = Placer(make_scene(restrictions=[zone]))
    assert placer.target_geometry("детская зона") is not None
    assert placer.target_geometry("ДЕТСКАЯ ЗОНА") is not None
    assert placer.target_geometry("no such zone") is None


def test_target_zones_allowed_zone_matched_by_name_like_a_manual_selection():
    """severity "allowed" -- ровно то, чем становится зона, которую пользователь
    выделяет мышкой на плане (см. issue "Выделение участка карты мышкой"):
    просто маркер с именем, а не запрет. Должна находиться по имени наравне с
    forbidden/warning-зонами от define_zone."""
    selection = make_zone(id="sel1", type="selection", name="Выделение", severity="allowed", min_distance=0.0, polygon=[
        Point2(x=10, z=10), Point2(x=20, z=10), Point2(x=20, z=20)
    ])
    placer = Placer(make_scene(restrictions=[selection]))
    assert placer.target_geometry("Выделение") is not None
    assert placer.target_geometry("выделение") is not None


def test_region_ignores_allowed_zone_it_does_not_block_placement():
    allowed = make_zone(id="a1", type="selection", name="Выделение", severity="allowed", min_distance=0.0, polygon=[
        Point2(x=-5, z=-5), Point2(x=5, z=-5), Point2(x=5, z=5), Point2(x=-5, z=5)
    ])
    with_zone = Placer(make_scene(restrictions=[allowed])).region("tree")[0]
    without_zone = Placer(make_scene(restrictions=[])).region("tree")[0]
    assert with_zone.equals(without_zone)


def test_target_geometry_site_boundary_is_the_site_polygon():
    placer = Placer(make_scene())
    assert placer.target_geometry("site_boundary") is placer.site


def test_target_geometry_pedestrian_path_includes_existing_path_segments():
    path_tile = make_object("path_1", "path_segment", 0, 0)
    placer = Placer(make_scene(objects=[path_tile]))
    geom = placer.target_geometry("pedestrian_path")
    assert geom is not None
    assert geom.contains(Point(0, 0))


def test_target_summary_site_boundary_reports_one_and_perimeter_length():
    placer = Placer(make_scene(boundary=make_boundary(0, 0, 10, 10)))
    count, length = placer.target_summary("site_boundary")
    assert count == 1
    assert length == pytest.approx(40.0)


def test_target_summary_non_boundary_target_reports_zone_count_and_perimeter():
    zone = make_zone(type="building", name="BUILDING_1")
    placer = Placer(make_scene(restrictions=[zone]))
    count, length = placer.target_summary("building")
    assert count == 1
    assert length == pytest.approx(40.0)  # периметр квадрата 10x10


def test_target_summary_none_for_missing_target():
    placer = Placer(make_scene())
    assert placer.target_summary("building") is None


def test_target_distance_site_boundary_uses_exterior_not_polygon_area():
    placer = Placer(make_scene(boundary=make_boundary(0, 0, 10, 10)))
    # Центр квадрата -- расстояние до ГРАНИЦЫ (5 м), а не 0 (внутри полигона).
    assert placer.target_distance("site_boundary", 5, 5) == pytest.approx(5.0)


def test_target_distance_none_for_missing_target():
    placer = Placer(make_scene())
    assert placer.target_distance("building", 0, 0) is None


# --- Placer.min_offset / points_along -----------------------------------------


def test_min_offset_site_boundary_uses_site_clearance():
    placer = Placer(make_scene())
    offset = placer.min_offset("site_boundary", "bush", half_depth=1.0)
    assert offset == pytest.approx(placement.SITE_CLEARANCE_M + 2 * placement.REGION_EPS_M + 0.8)


def test_min_offset_non_boundary_target_uses_required_setback_of_matched_zones():
    zone = make_zone(type="building")  # required("building", "bush") == 1.5
    placer = Placer(make_scene(restrictions=[zone]))
    offset = placer.min_offset("building", "bush", half_depth=1.0)
    assert offset == pytest.approx(1.5 + 2 * placement.REGION_EPS_M + 0.8)


def test_points_along_returns_empty_for_missing_target():
    placer = Placer(make_scene())
    assert placer.points_along("building", spacing=5.0, offset=1.0) == []


def test_points_along_site_boundary_produces_points_with_tangent_rotation():
    placer = Placer(make_scene(boundary=make_boundary(0, 0, 100, 100)))
    points = placer.points_along("site_boundary", spacing=10.0, offset=2.0)
    assert len(points) > 0
    for x, z, rotation in points:
        assert 0 <= x <= 100
        assert 0 <= z <= 100
        assert isinstance(rotation, float)


def test_points_along_skips_short_rings():
    tiny_zone = make_zone(type="building", polygon=[
        Point2(x=0, z=0), Point2(x=0.1, z=0), Point2(x=0.1, z=0.1)
    ])
    placer = Placer(make_scene(restrictions=[tiny_zone]))
    # Контур настолько мал, что после buffer(offset) кольцо короче 1 м -- пропущено.
    assert placer.points_along("building", spacing=1.0, offset=0.1) == []


# --- Placer.points_in_area / free_areas ---------------------------------------


def test_points_in_area_returns_points_inside_region():
    placer = Placer(make_scene())
    points = placer.points_in_area("tree", step=10.0)
    assert len(points) > 0
    for x, z in points:
        assert placer.in_region(x, z, "tree")


def test_points_in_area_returns_empty_when_within_does_not_overlap_region():
    placer = Placer(make_scene())
    far_away = Polygon([(1000, 1000), (1001, 1000), (1001, 1001), (1000, 1001)])
    assert placer.points_in_area("tree", step=5.0, within=far_away) == []


def test_points_in_area_none_region_returns_empty():
    huge_building = make_zone(
        type="building", severity="forbidden", min_distance=0.0,
        polygon=[Point2(x=-49, z=-49), Point2(x=49, z=-49), Point2(x=49, z=49), Point2(x=-49, z=49)],
    )
    placer = Placer(make_scene(restrictions=[huge_building]))
    assert placer.points_in_area("tree", step=5.0) == []


def test_points_in_area_respects_within_polygon():
    placer = Placer(make_scene())
    within = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    points = placer.points_in_area("tree", step=2.0, within=within)
    assert len(points) > 0
    for x, z in points:
        assert 0 <= x <= 10
        assert 0 <= z <= 10


def test_free_areas_sorted_by_size_and_limited():
    placer = Placer(make_scene())
    areas = placer.free_areas(limit=1)
    assert len(areas) == 1


def test_free_areas_excludes_small_fragments():
    placer = Placer(make_scene())
    areas = placer.free_areas(min_area_m2=1e9)  # ничего не пройдёт порог
    assert areas == []


def test_free_areas_is_cached():
    placer = Placer(make_scene())
    first = placer.free_areas()
    second = placer.free_areas()
    assert first is second


# --- spread_subset / pick_near / pick_spread -----------------------------------


def test_spread_subset_returns_all_when_within_limit():
    points = [(i, i) for i in range(5)]
    assert spread_subset(points, 10) == points


def test_spread_subset_samples_evenly_across_the_list():
    points = [(i, 0) for i in range(10)]
    subset = spread_subset(points, 5)
    assert len(subset) == 5
    assert subset[0] == points[0]


def test_pick_near_orders_by_distance_to_center():
    points = [(10, 0), (1, 0), (5, 0)]
    chosen = pick_near(points, count=3, min_distance=0.5, center=(0, 0))
    assert chosen[0] == (1, 0)


def test_pick_near_respects_minimum_distance():
    points = [(0, 0), (0.1, 0), (5, 0)]
    chosen = pick_near(points, count=3, min_distance=1.0, center=(0, 0))
    assert len(chosen) == 2  # (0,0) и (0.1,0) слишком близки друг к другу


def test_pick_near_stops_once_count_is_reached_with_points_left_over():
    # Кандидатов заведомо больше, чем count -- после набора нужного числа
    # оставшиеся (более дальние) точки не должны даже проверяться.
    points = [(i, 0) for i in range(20)]
    chosen = pick_near(points, count=3, min_distance=1.0, center=(0, 0))
    assert len(chosen) == 3
    assert chosen == [(0, 0), (1, 0), (2, 0)]


def test_pick_spread_returns_all_via_greedy_when_not_more_than_count():
    points = [(0, 0), (10, 10)]
    assert set(pick_spread(points, count=5, min_distance=1.0)) == set(points)


def test_pick_spread_is_deterministic_across_calls():
    points = [(i * 3.0, (i % 4) * 2.0) for i in range(40)]
    first = pick_spread(points, count=8, min_distance=1.0)
    second = pick_spread(points, count=8, min_distance=1.0)
    assert first == second


def test_pick_spread_respects_requested_count_upper_bound():
    points = [(i * 1.0, 0.0) for i in range(50)]
    chosen = pick_spread(points, count=10, min_distance=0.1)
    assert len(chosen) == 10


def test_pick_spread_backfills_when_clusters_cannot_fit_requested_count():
    # Два плотных кластера (внутри каждого точки ближе min_distance друг к
    # другу) -- k-средних метит по 6 центров на каждый, но большинство их
    # кандидатов будут отвергнуты как "слишком близко к уже выбранному"
    # (line 569) -- реально влезет по 1 точке на кластер, backfill (line 583)
    # честно перебирает остальные и тоже их все отвергает.
    points = [(0.01 * i, 0.0) for i in range(15)] + [(100 + 0.01 * i, 0.0) for i in range(15)]
    chosen = pick_spread(points, count=12, min_distance=0.5)
    assert len(chosen) == 2  # больше просто негде -- оба кластера предельно плотные


def test_pick_spread_spans_more_area_than_naive_prefix():
    # Точки идут вдоль длинной линии -- pick_spread должен раскидать выбор по
    # всей длине, а не взять первые count кучей в начале.
    points = [(float(i), 0.0) for i in range(100)]
    chosen = pick_spread(points, count=5, min_distance=1.0)
    xs = sorted(x for x, _ in chosen)
    assert xs[-1] - xs[0] > 50  # разброс заметно больше, чем у "первых 5"


# --- _segment_visible / shortest_path ------------------------------------------


def test_segment_visible_true_with_no_obstacles():
    assert _segment_visible(Point(0, 0), Point(10, 0), []) is True


def test_segment_visible_false_when_crossing_an_obstacle():
    obstacle = Polygon([(4, -4), (6, -4), (6, 4), (4, 4)])
    assert _segment_visible(Point(0, 0), Point(10, 0), [obstacle]) is False


def test_segment_visible_true_when_only_touching_a_corner():
    # Линия идёт точно через угол препятствия, не проникая внутрь его площади.
    obstacle = Polygon([(5, 0), (10, 5), (5, 10), (0, 5)])  # ромб с углом в (5,0)
    assert _segment_visible(Point(0, 0), Point(10, 0), [obstacle]) is True


def test_segment_visible_true_for_coincident_points():
    p = Point(5, 5)
    obstacle = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    assert _segment_visible(p, p, [obstacle]) is True


def test_shortest_path_direct_line_without_obstacles():
    path = shortest_path(Point(0, 0), Point(10, 0), [])
    assert list(path.coords) == [(0.0, 0.0), (10.0, 0.0)]


def test_shortest_path_routes_around_a_single_obstacle():
    obstacle = Polygon([(4, -4), (6, -4), (6, 4), (4, 4)])
    path = shortest_path(Point(0, 0), Point(10, 0), [obstacle])
    assert path is not None
    assert len(path.coords) > 2  # обошёл, не прямая
    # Путь не должен насквозь проходить через препятствие.
    from shapely.geometry import LineString

    line = LineString(path.coords)
    assert line.intersection(obstacle).area == 0


def test_shortest_path_returns_none_when_target_is_unreachable():
    # Конец строго внутри препятствия -- любая линия к нему пересекает его по
    # площади, видимости нет ни с одного узла графа.
    obstacle = Polygon([(-20, -20), (20, -20), (20, 20), (-20, 20)])
    path = shortest_path(Point(-100, 0), Point(0, 0), [obstacle])
    assert path is None


def test_shortest_path_truncates_obstacle_list_beyond_the_limit(monkeypatch):
    monkeypatch.setattr(placement, "MAX_OBSTACLES_FOR_ROUTING", 1)
    obstacle_blocking = Polygon([(4, -4), (6, -4), (6, 4), (4, 4)])
    far_away_obstacle = Polygon([(1000, 1000), (1001, 1000), (1001, 1001)])
    # С обрезкой до 1 препятствия учитывается только первое -- маршрут всё
    # равно должен успешно обойти его, не упав из-за "слишком много препятствий".
    path = shortest_path(Point(0, 0), Point(10, 0), [obstacle_blocking, far_away_obstacle])
    assert path is not None


def test_shortest_path_visible_straight_line_ignores_far_obstacles():
    far_obstacle = Polygon([(1000, 1000), (1001, 1000), (1001, 1001)])
    path = shortest_path(Point(0, 0), Point(10, 0), [far_obstacle])
    assert list(path.coords) == [(0.0, 0.0), (10.0, 0.0)]
