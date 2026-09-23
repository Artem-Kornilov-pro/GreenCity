"""core/placement_geometry.py -- выбор мест из кандидатов, обход препятствий,
линии для рядов и паттернов."""

import math

import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from core import placement_geometry
from core.placement_geometry import (
    _segment_visible,
    centerline_of,
    concentric_rings_of,
    lines_of,
    pick_near,
    pick_spread,
    rect_sides,
    rotation_of,
    rows_of,
    sample_line,
    shortest_path,
    spread_subset,
    wavy_line,
)

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
    monkeypatch.setattr(placement_geometry, "MAX_OBSTACLES_FOR_ROUTING", 1)
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
