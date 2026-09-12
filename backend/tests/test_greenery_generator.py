"""greenery_generator.py -- деревья/кусты/газон по сетке. Сцены строятся
руками (helpers.py) для точных ожиданий; локации из conftest.py -- для
сквозной геометрической проверки на настоящих участках (0 нарушений норм)."""

import greenery_generator as gg
import pytest
from helpers import make_object, make_scene, make_zone
from schemas import Point2
from setback_norms import setback_for
from shapely.geometry import Point, Polygon, box


def _lawn_zone(x0=-50, z0=-50, x1=50, z1=50):
    return make_zone(id="lawn", type="protected_zone", name="GRASS", severity="allowed", min_distance=0.0, polygon=[
        Point2(x=x0, z=z0), Point2(x=x1, z=z0), Point2(x=x1, z=z1), Point2(x=x0, z=z1)
    ])


# --- Общие приватные хелперы ---------------------------------------------


def test_keep_out_shapes_includes_forbidden_and_warning_not_allowed():
    forbidden = make_zone(id="f", type="building", severity="forbidden")
    warning = make_zone(id="w", type="pedestrian_path", severity="warning", min_distance=0.5, polygon=[
        Point2(x=20, z=20), Point2(x=30, z=20), Point2(x=30, z=30)
    ])
    allowed = _lawn_zone()
    shapes = gg._keep_out_shapes([forbidden, warning, allowed], "tree")
    assert len(shapes) == 2


def test_keep_out_shapes_skips_zone_with_too_few_points():
    good = make_zone(id="f", type="building", severity="forbidden")
    degenerate = make_zone(id="deg", type="building", severity="forbidden", polygon=[Point2(x=0, z=0), Point2(x=1, z=0)])
    shapes = gg._keep_out_shapes([good, degenerate], "tree")
    assert len(shapes) == 1


def test_keep_out_shapes_skips_zero_area_collinear_zone():
    good = make_zone(id="f", type="building", severity="forbidden")
    collinear = make_zone(id="col", type="building", severity="forbidden", polygon=[
        Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=10, z=0)
    ])
    shapes = gg._keep_out_shapes([good, collinear], "tree")
    assert len(shapes) == 1


def test_keep_out_shapes_buffers_by_required_setback():
    zone = make_zone(type="building")
    shapes = gg._keep_out_shapes([zone], "tree")
    # Зона [-5,5]x[-5,5], отступ дерева от здания 5 м -- буфер должен покрыть (9.9, 0).
    assert shapes[0].contains(Point(9.9, 0))
    assert not shapes[0].contains(Point(10.1, 0))


def test_keep_out_shapes_species_override_changes_buffer(monkeypatch):
    from setback_norms import SPECIES_SETBACK_OVERRIDES

    monkeypatch.setitem(SPECIES_SETBACK_OVERRIDES, "тополь чёрный", {"building": 8.0})
    zone = make_zone(type="building")
    shapes = gg._keep_out_shapes([zone], "tree", species="тополь чёрный")
    assert shapes[0].contains(Point(12.9, 0))


def test_raw_zone_shapes_ignores_plant_setback_entirely():
    zone = make_zone(type="building")  # дерево -- 5м, но _raw_zone_shapes это не учитывает
    shapes = gg._raw_zone_shapes([zone])
    assert not shapes[0].contains(Point(6, 0))  # чуть за пределами самой зоны [-5,5] -- уже свободно
    assert shapes[0].contains(Point(4, 0))


def test_raw_zone_shapes_skips_allowed_zones():
    assert gg._raw_zone_shapes([_lawn_zone()]) == []


def test_planting_zone_shapes_only_allowed_zones_with_valid_polygons():
    allowed = _lawn_zone()
    forbidden = make_zone(id="f", type="building", severity="forbidden")
    shapes = gg._planting_zone_shapes([allowed, forbidden])
    assert len(shapes) == 1


def test_existing_object_shapes_excludes_buildings():
    building = make_object("b1", "building", 0, 0)
    bench = make_object("bench1", "bench", 10, 10)
    shapes = gg._existing_object_shapes([building, bench], clearance=1.0)
    assert len(shapes) == 1
    assert shapes[0].contains(Point(10.5, 10))


def test_existing_object_shapes_uses_custom_clearance():
    bench = make_object("bench1", "bench", 0, 0)
    shapes = gg._existing_object_shapes([bench], clearance=0.3)
    assert shapes[0].area == pytest.approx(Point(0, 0).buffer(0.3).area)


def test_placement_reason_mentions_nearest_zone_and_distance():
    zone = make_zone(type="building", name="BUILDING_1")
    reasons = gg._placement_reason(10, 0, [zone])
    assert "внутри допустимой зоны озеленения" in reasons[0]
    assert any("BUILDING_1" in r for r in reasons)


def test_placement_reason_handles_no_zones_at_all():
    reasons = gg._placement_reason(0, 0, [])
    assert reasons == ["внутри допустимой зоны озеленения"]


def test_placement_reason_skips_degenerate_and_invalid_zones():
    too_few_points = make_zone(type="building", name="BAD1", polygon=[Point2(x=0, z=0), Point2(x=1, z=0)])
    self_intersecting = make_zone(type="building", name="BAD2", polygon=[
        Point2(x=0, z=0), Point2(x=10, z=10), Point2(x=10, z=0), Point2(x=0, z=10)
    ])
    good = make_zone(type="building", name="GOOD")
    reasons = gg._placement_reason(20, 0, [too_few_points, self_intersecting, good])
    assert any("GOOD" in r for r in reasons)
    assert not any("BAD1" in r or "BAD2" in r for r in reasons)


# --- generate_trees ------------------------------------------------------


def test_generate_trees_returns_empty_without_boundary():
    scene = make_scene(boundary=None)
    assert gg.generate_trees(scene) == []


def test_generate_trees_places_within_lawn_and_avoids_building():
    building = make_zone(type="building")
    scene = make_scene(restrictions=[_lawn_zone(), building])
    trees = gg.generate_trees(scene, grid_spacing_m=5.0)
    assert len(trees) > 0
    for t in trees:
        assert t.type == "tree"
        dist_to_building = Point(t.position.x, t.position.z).distance(Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)]))
        assert dist_to_building >= setback_for("building", "tree", 1.5) - 1e-6


def test_generate_trees_respects_min_spacing():
    scene = make_scene(restrictions=[_lawn_zone()])
    trees = gg.generate_trees(scene, grid_spacing_m=2.0, min_tree_spacing_m=6.0)
    positions = [(t.position.x, t.position.z) for t in trees]
    for i, p1 in enumerate(positions):
        for p2 in positions[i + 1 :]:
            assert Point(p1).distance(Point(p2)) >= 6.0 - 1e-6


def test_generate_trees_ids_continue_after_existing_trees():
    existing = make_object("tree_gen_001", "tree", 0, 0)
    scene = make_scene(restrictions=[_lawn_zone()], objects=[existing])
    trees = gg.generate_trees(scene, grid_spacing_m=10.0)
    assert all(int(t.id.split("_")[-1]) > 1 for t in trees)


def test_generate_trees_falls_back_to_whole_site_without_lawn_zones():
    scene = make_scene()  # без зон вообще -- откат на весь участок минус keep_out
    trees = gg.generate_trees(scene, grid_spacing_m=10.0)
    assert len(trees) > 0


def test_generate_trees_grid_spacing_is_clamped_to_allowed_range():
    scene = make_scene(restrictions=[_lawn_zone()])
    trees_tiny_spacing = gg.generate_trees(scene, grid_spacing_m=0.001)
    trees_default = gg.generate_trees(scene, grid_spacing_m=gg.MIN_ALLOWED_GRID_SPACING_M)
    assert len(trees_tiny_spacing) == len(trees_default)


def test_generate_trees_species_metadata_is_set():
    scene = make_scene(restrictions=[_lawn_zone()])
    trees = gg.generate_trees(scene, species="Дуб", grid_spacing_m=20.0)
    assert all(t.metadata["species"] == "Дуб" for t in trees)


def test_generate_trees_returns_empty_when_area_fully_occupied():
    huge_building = make_zone(type="building", severity="forbidden", min_distance=0.0, polygon=[
        Point2(x=-49, z=-49), Point2(x=49, z=-49), Point2(x=49, z=49), Point2(x=-49, z=49)
    ])
    scene = make_scene(restrictions=[huge_building])
    assert gg.generate_trees(scene) == []


def test_generate_trees_zero_area_boundary_returns_empty():
    from schemas import Boundary

    degenerate = Boundary(polygon=[Point2(x=0, z=0), Point2(x=1, z=0)], sourceLayer="X")
    scene = make_scene(boundary=degenerate)
    assert gg.generate_trees(scene) == []


def test_generate_trees_collinear_boundary_has_zero_area():
    from schemas import Boundary

    collinear = Boundary(polygon=[Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=10, z=0)], sourceLayer="X")
    assert gg.generate_trees(make_scene(boundary=collinear)) == []


# --- generate_bushes -------------------------------------------------------


def test_generate_bushes_returns_empty_without_boundary():
    assert gg.generate_bushes(make_scene(boundary=None)) == []


def test_generate_bushes_produces_full_clusters_of_three():
    scene = make_scene(restrictions=[_lawn_zone()])
    bushes = gg.generate_bushes(scene, grid_spacing_m=5.0)
    assert len(bushes) % gg.BUSH_CLUSTER_SIZE == 0
    assert len(bushes) > 0
    assert all(b.type == "bush" for b in bushes)


def test_generate_bushes_avoids_building_with_bush_setback():
    building = make_zone(type="building")  # куст -- отступ 1.5 м от здания
    scene = make_scene(restrictions=[_lawn_zone(), building])
    bushes = gg.generate_bushes(scene, grid_spacing_m=2.0)
    footprint = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)])
    for b in bushes:
        assert Point(b.position.x, b.position.z).distance(footprint) >= 1.5 - 1e-6


def test_generate_bushes_caps_total_clusters(monkeypatch):
    monkeypatch.setattr(gg, "MAX_GENERATED_BUSH_CLUSTERS", 2)
    scene = make_scene(restrictions=[_lawn_zone()])
    bushes = gg.generate_bushes(scene, grid_spacing_m=3.0, min_bush_spacing_m=3.0)
    assert len(bushes) == 2 * gg.BUSH_CLUSTER_SIZE


def test_generate_bushes_rejects_cluster_when_any_member_falls_outside_area():
    # Газон -- узкая полоска у самого края участка: центр кластера ещё
    # помещается, но один из трёх кустов по кругу уже вываливается за
    # пределы допустимой площади -- вся группа должна быть отклонена.
    narrow_lawn = make_zone(id="lawn", type="protected_zone", name="GRASS", severity="allowed", min_distance=0.0, polygon=[
        Point2(x=-1.0, z=-50), Point2(x=1.0, z=-50), Point2(x=1.0, z=50), Point2(x=-1.0, z=50)
    ])
    scene = make_scene(restrictions=[narrow_lawn])
    bushes = gg.generate_bushes(scene, grid_spacing_m=5.0)
    assert bushes == []


def test_generate_bushes_ids_continue_after_existing_bush_count():
    # Нумерация продолжает КОЛИЧЕСТВО уже существующих кустов в сцене, а не
    # разбирает суффикс их id -- см. существующий generate_trees, тот же
    # приём (existing_tree_count = sum(...)).
    existing = [make_object(f"bush_manual_{i}", "bush", 40, 40 + i) for i in range(3)]
    scene = make_scene(restrictions=[_lawn_zone()], objects=existing)
    bushes = gg.generate_bushes(scene, grid_spacing_m=10.0)
    assert bushes  # что-то сгенерировалось
    assert all(int(b.id.split("_")[-1]) > 3 for b in bushes)


def test_generate_bushes_zero_area_boundary_returns_empty():
    from schemas import Boundary

    degenerate = Boundary(polygon=[Point2(x=0, z=0), Point2(x=1, z=0)], sourceLayer="X")
    assert gg.generate_bushes(make_scene(boundary=degenerate)) == []


def test_generate_bushes_collinear_boundary_has_zero_area():
    from schemas import Boundary

    collinear = Boundary(polygon=[Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=10, z=0)], sourceLayer="X")
    assert gg.generate_bushes(make_scene(boundary=collinear)) == []


def test_generate_bushes_returns_empty_when_area_fully_occupied():
    huge_building = make_zone(type="building", severity="forbidden", min_distance=0.0, polygon=[
        Point2(x=-49, z=-49), Point2(x=49, z=-49), Point2(x=49, z=49), Point2(x=-49, z=49)
    ])
    assert gg.generate_bushes(make_scene(restrictions=[huge_building])) == []


# --- generate_lawn ----------------------------------------------------------


def test_generate_lawn_returns_empty_without_boundary():
    assert gg.generate_lawn(make_scene(boundary=None)) == []


def test_generate_lawn_tiles_do_not_overlap():
    scene = make_scene(restrictions=[_lawn_zone()])
    tiles = gg.generate_lawn(scene, patch_size_m=4.0)
    assert len(tiles) > 0
    boxes = [box(t.position.x - 2, t.position.z - 2, t.position.x + 2, t.position.z + 2) for t in tiles]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a.intersection(b).area < 1e-6


def test_generate_lawn_avoids_forbidden_zone_without_extra_plant_setback():
    building = make_zone(type="building")  # [-5,5]x[-5,5]
    scene = make_scene(restrictions=[_lawn_zone(), building])
    tiles = gg.generate_lawn(scene, patch_size_m=4.0)
    footprint = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)])
    for t in tiles:
        tile_box = box(t.position.x - 2, t.position.z - 2, t.position.x + 2, t.position.z + 2)
        assert tile_box.intersection(footprint).area < 1e-6


def test_generate_lawn_patch_size_is_clamped():
    scene = make_scene(restrictions=[_lawn_zone()])
    tiles_huge = gg.generate_lawn(scene, patch_size_m=1000.0)
    tiles_clamped = gg.generate_lawn(scene, patch_size_m=gg.MAX_ALLOWED_LAWN_PATCH_SIZE_M)
    assert len(tiles_huge) == len(tiles_clamped)


def test_generate_lawn_caps_total_patches(monkeypatch):
    monkeypatch.setattr(gg, "MAX_GENERATED_LAWN_PATCHES", 3)
    scene = make_scene(restrictions=[_lawn_zone()])
    tiles = gg.generate_lawn(scene, patch_size_m=4.0)
    assert len(tiles) == 3


def test_generate_lawn_ids_continue_after_existing_patch_count():
    existing = [make_object(f"lawn_manual_{i}", "lawn_patch", 40, 40 + i) for i in range(2)]
    scene = make_scene(restrictions=[_lawn_zone()], objects=existing)
    tiles = gg.generate_lawn(scene, patch_size_m=8.0)
    assert tiles
    assert all(int(t.id.split("_")[-1]) > 2 for t in tiles)


def test_generate_lawn_zero_area_boundary_returns_empty():
    from schemas import Boundary

    degenerate = Boundary(polygon=[Point2(x=0, z=0), Point2(x=1, z=0)], sourceLayer="X")
    assert gg.generate_lawn(make_scene(boundary=degenerate)) == []


def test_generate_lawn_collinear_boundary_has_zero_area():
    from schemas import Boundary

    collinear = Boundary(polygon=[Point2(x=0, z=0), Point2(x=5, z=0), Point2(x=10, z=0)], sourceLayer="X")
    assert gg.generate_lawn(make_scene(boundary=collinear)) == []


def test_generate_lawn_returns_empty_when_area_fully_occupied():
    # generate_lawn не добавляет отступа к запретным зонам (в отличие от
    # деревьев/кустов, см. _raw_zone_shapes) -- поэтому, в отличие от
    # аналогичных тестов для деревьев/кустов, здание должно перекрыть
    # ВЕСЬ участок целиком, а не почти весь, чтобы разница стала пустой.
    full_site_building = make_zone(type="building", severity="forbidden", min_distance=0.0, polygon=[
        Point2(x=-50, z=-50), Point2(x=50, z=-50), Point2(x=50, z=50), Point2(x=-50, z=50)
    ])
    assert gg.generate_lawn(make_scene(restrictions=[full_site_building])) == []


# --- Сквозная проверка на настоящих участках --------------------------------


@pytest.mark.parametrize("n", [1, 2, 3, 4, 6])
def test_full_pipeline_on_real_locations_has_zero_setback_violations(location_scene, n):
    scene = location_scene[n].model_copy(deep=True)

    trees = gg.generate_trees(scene)
    scene.objects = [*scene.objects, *trees]
    for t in trees:
        p = Point(t.position.x, t.position.z)
        for zone in scene.restrictions:
            if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
                continue
            poly = Polygon([(pt.x, pt.z) for pt in zone.polygon])
            if not poly.is_valid:
                continue
            required = setback_for(zone.type, "tree", zone.minDistance)
            assert p.distance(poly) >= required - 1e-6

    bushes = gg.generate_bushes(scene)
    scene.objects = [*scene.objects, *bushes]
    for b in bushes:
        p = Point(b.position.x, b.position.z)
        for zone in scene.restrictions:
            if zone.severity not in ("forbidden", "warning") or len(zone.polygon) < 3:
                continue
            poly = Polygon([(pt.x, pt.z) for pt in zone.polygon])
            if not poly.is_valid:
                continue
            required = setback_for(zone.type, "bush", zone.minDistance)
            assert p.distance(poly) >= required - 1e-6

    lawn = gg.generate_lawn(scene)
    lawn_boxes = [box(t.position.x - 2, t.position.z - 2, t.position.x + 2, t.position.z + 2) for t in lawn]
    for i, a in enumerate(lawn_boxes):
        for b in lawn_boxes[i + 1 :]:
            assert a.intersection(b).area < 1e-6
