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
    centerline_of,
    clamp,
    concentric_rings_of,
    lines_of,
    pick_near,
    pick_spread,
    polygons,
    rect_sides,
    rotation_of,
    rows_of,
    sample_line,
    shortest_path,
    spread_subset,
    wavy_line,
)
from schemas import Point2
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

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


# --- lines_of / rotation_of / sample_line / rect_sides / centerline_of --------
#
# Изначально были частными хелперами courtyard_design.py (единственного тогда
# потребителя); переехали сюда, когда deterministic_placement.py понадобилась
# та же самая логика линейных паттернов (см. docstring centerline_of).


def test_lines_of_extracts_linestring_and_linearring():
    ls = LineString([(0, 0), (1, 1)])
    assert lines_of(ls) == [ls]
    ring = Polygon([(0, 0), (1, 0), (1, 1)]).exterior
    assert len(lines_of(ring)) == 1


def test_lines_of_handles_none_and_empty():
    assert lines_of(None) == []
    assert lines_of(LineString()) == []


def test_lines_of_flattens_multilinestring():
    mls = MultiLineString([[(0, 0), (1, 0)], [(2, 2), (3, 3)]])
    assert len(lines_of(mls)) == 2


def test_rotation_of_matches_points_along_convention():
    assert rotation_of(1.0, 0.0) == pytest.approx(0.0)
    assert rotation_of(0.0, 0.0) == 0.0
    assert rotation_of(0.0, -1.0) == pytest.approx(90.0)


def test_sample_line_yields_evenly_spaced_points_with_unit_tangent():
    line = LineString([(0, 0), (10, 0)])
    points = list(sample_line(line, step=5.0))
    assert len(points) == 2
    for _x, _z, tx, tz in points:
        assert math.hypot(tx, tz) == pytest.approx(1.0)
        assert tz == pytest.approx(0.0)


def test_sample_line_empty_for_degenerate_line():
    assert list(sample_line(LineString([(0, 0), (0, 0)]), step=1.0)) == []


def test_rect_sides_returns_two_perpendicular_unit_vectors():
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    sides = rect_sides(square)
    assert len(sides) == 2
    (ux1, uz1, len1), (ux2, uz2, len2) = sides
    assert len1 == pytest.approx(10.0)
    assert len2 == pytest.approx(10.0)
    assert ux1 * ux2 + uz1 * uz2 == pytest.approx(0.0, abs=1e-6)  # перпендикулярны


def test_centerline_of_spans_the_long_axis_of_a_band():
    # Полоса 40x4 -- centerline должен пройти вдоль длинной стороны (40 м),
    # а не короткой, и почти по всей её длине.
    band = Polygon([(0, 0), (40, 0), (40, 4), (0, 4)])
    line = centerline_of(band)
    assert line is not None
    assert lines_of(line)[0].length == pytest.approx(40.0, rel=0.01)


def test_centerline_of_none_for_degenerate_polygon():
    assert centerline_of(Polygon()) is None


def test_centerline_of_bent_band_yields_multiple_segments():
    # L-образная полоса -- прямая через центроид вдоль "главной" оси выходит
    # за пределы контура и возвращается назад, поэтому пересечение с полигоном
    # распадается на несколько кусков (см. docstring centerline_of).
    bent = Polygon([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)])
    line = centerline_of(bent)
    assert line is not None
    assert len(lines_of(line)) >= 1


def test_centerline_of_angle_offset_gives_diagonal_line():
    # Issue "не только прямые засадки" -- diagonal_rows проводит линию под
    # углом к длинной оси зоны, а не вдоль неё. На квадрате поворот на 45°
    # должен дать диагональ угол-в-угол, а не ту же вертикаль/горизонталь.
    square = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    straight = centerline_of(square, angle_offset_deg=0.0)
    diagonal = centerline_of(square, angle_offset_deg=45.0)
    assert straight is not None and diagonal is not None
    sx0, sz0 = straight.coords[0]
    sx1, sz1 = straight.coords[-1]
    dx0, dz0 = diagonal.coords[0]
    dx1, dz1 = diagonal.coords[-1]
    # Прямая идёт вдоль одной из осей (x или z почти не меняется), диагональ --
    # нет (оба конца заметно различаются и по x, и по z).
    assert abs(sx0 - sx1) < 1e-6 or abs(sz0 - sz1) < 1e-6
    assert abs(dx0 - dx1) > 15.0
    assert abs(dz0 - dz1) > 15.0


def test_rows_of_gives_multiple_rows_across_zone_width():
    # Issue "не заполняет всё пространство" -- одна линия через центр
    # покрывала только середину широкой open_area-зоны. Полоса 60x24 при
    # шаге 6 м должна дать несколько рядов, не один.
    band = Polygon([(0, 0), (60, 0), (60, 24), (0, 24)])
    rows = rows_of(band, spacing=6.0)
    assert len(rows) >= 3
    # Ряды реально разнесены по короткой стороне (z), а не совпадают.
    z_positions = sorted({round(lines_of(r)[0].coords[0][1], 1) for r in rows})
    assert len(z_positions) == len(rows)


def test_rows_of_single_row_for_narrow_band():
    # Узкая полоса (4 м, как у building_border/path_corridor) -- рядов не
    # больше 1 и на достаточно большом шаге, тот же случай, что раньше
    # покрывался одной centerline_of().
    band = Polygon([(0, 0), (40, 0), (40, 4), (0, 4)])
    rows = rows_of(band, spacing=6.0)
    assert len(rows) == 1


def test_concentric_rings_of_gives_multiple_growing_radii():
    # Компактная площадь 60x60 -- несколько вложенных колец вокруг центра
    # (concentric_rings), не одна линия и не одно кольцо по контуру зоны
    # (line_shape="ring", building_ring).
    square = Polygon([(0, 0), (60, 0), (60, 60), (0, 60)])
    rings = concentric_rings_of(square, spacing=6.0)
    assert len(rings) >= 3
    center = square.centroid
    # Каждое следующее кольцо реально дальше от центра, чем предыдущее.
    radii = [max(Point(c).distance(center) for c in lines_of(r)[0].coords) for r in rings]
    assert radii == sorted(radii)
    assert radii[0] < radii[-1]


def test_concentric_rings_of_stays_within_the_zone_when_clipped():
    # Неквадратная (треугольная) зона -- кольца обрезаются по контуру, а не
    # выходят за его пределы, как rows_of/centerline_of для своих линий.
    triangle = Polygon([(0, 0), (40, 0), (20, 34)])
    rings = concentric_rings_of(triangle, spacing=5.0)
    assert rings
    for ring in rings:
        for line in lines_of(ring):
            for coord in line.coords:
                assert triangle.buffer(1e-6).contains(Point(coord))


def test_concentric_rings_of_empty_for_degenerate_polygon():
    assert concentric_rings_of(Polygon(), spacing=6.0) == []


def test_wavy_line_oscillates_by_amplitude_perpendicular_to_direction():
    # Issue "не только прямые засадки" -- flowing_rows (10_stary_gay) должен
    # быть волнистым, не прямым. Прямая вдоль x, изогнутая по z на амплитуду
    # 4 м -- координата z всех точек должна колебаться в пределах +-4 м
    # (с небольшим запасом на численную интерполяцию), а не быть константой.
    line = LineString([(0, 0), (100, 0)])
    wavy = wavy_line(line, amplitude=4.0, wavelength=40.0)
    zs = [c[1] for c in wavy.coords]
    assert max(zs) > 3.5
    assert min(zs) < -3.5
    assert max(zs) <= 4.01 and min(zs) >= -4.01


def test_wavy_line_zero_amplitude_returns_line_unchanged():
    line = LineString([(0, 0), (100, 0)])
    assert wavy_line(line, amplitude=0.0, wavelength=40.0) is line


def test_wavy_line_none_or_empty_is_safe():
    assert wavy_line(None, amplitude=4.0, wavelength=40.0) is None
    empty = LineString()
    assert wavy_line(empty, amplitude=4.0, wavelength=40.0) is empty


# --- Правила отступа по породе (setback_norms.SPECIES_SETBACK_RULES) ---------


def test_is_free_applies_wide_crown_rule_for_linden():
    # Здание [-5,5]x[-5,5]. x=12 -- 7 м от стены: берёзе (5 м) можно, липе
    # (10 м, 743-ПП, табл. 3.6.1, прим. 3) -- нет; x=16 -- 11 м, можно обеим.
    placer = Placer(make_scene(restrictions=[make_zone(type="building")]))
    assert placer.is_free(12, 0, "tree", species="Берёза повислая")
    assert not placer.is_free(12, 0, "tree", species="Липа мелколистная")
    assert placer.is_free(16, 0, "tree", species="Липа мелколистная")


def test_is_free_without_species_keeps_table_norm():
    placer = Placer(make_scene(restrictions=[make_zone(type="building")]))
    assert placer.is_free(12, 0, "tree")


def test_is_free_species_pool_takes_strictest():
    placer = Placer(make_scene(restrictions=[make_zone(type="building")]))
    assert not placer.is_free(12, 0, "tree", species=["Берёза повислая", "Липа мелколистная"])


def test_region_cache_is_shared_by_species_without_rules():
    # Кэш -- по набору правил, а не по названию: дорогой unary_union не
    # должен пересчитываться на каждый из сотни видов каталога.
    # У берёзы правило есть, но только для теплосети (МГСН 4.2.8) -- на
    # участке без теплосети она делит область с рябиной (правил нет вовсе).
    placer = Placer(make_scene(restrictions=[make_zone(type="building")]))
    assert placer.region("tree", "Берёза повислая") is placer.region("tree", "Рябина обыкновенная")
    assert placer.region("tree", "Липа мелколистная") is not placer.region("tree", "Берёза повислая")
    heat = make_zone(id="h1", type="heat_network", name="HEAT", polygon=[Point2(x=20, z=-1), Point2(x=40, z=-1), Point2(x=40, z=1), Point2(x=20, z=1)])
    placer = Placer(make_scene(restrictions=[make_zone(type="building"), heat]))
    assert placer.region("tree", "Берёза повислая") is not placer.region("tree", "Рябина обыкновенная")


def test_signal_cable_zone_needs_2m_for_a_tree():
    # Раньше signal_cable не было в таблице отступов, и дерево вставало в
    # 0.5 м от края коридора кабеля связи (СП 42 требует 2 м).
    cable = make_zone(type="signal_cable", name="ELECTR_CABLE_COMM", severity="warning", min_distance=0.5)
    placer = Placer(make_scene(restrictions=[cable]))
    assert not placer.is_free(6.5, 0, "tree")
    assert placer.is_free(7.5, 0, "tree")
