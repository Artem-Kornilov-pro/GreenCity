"""site_characterization.characterize_site -- признаки участка для будущего
Этапа 3 GreenPlan (retrieval). Сверяется на реальных тестовых участках
(locations/location_old/), т.к. синтетических Scene с валидной геометрией
границ вручную не построить дёшево -- только эти шесть у нас и есть."""

from schemas import Boundary, Point2, RestrictionZone, Scene, SceneMeta
from site_characterization import characterize_site, usable_planting_area


def _meta(**overrides):
    base = dict(scale=1.0, insunits=6, origin={"x": 0.0, "y": 0.0}, buildingCount=0, pointObjectCount=0)
    base.update(overrides)
    return SceneMeta(**base)


def test_returns_none_without_boundary():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert characterize_site(scene) is None


def test_area_and_ratio_are_consistent(location_scene):
    for scene in location_scene.values():
        result = characterize_site(scene)
        assert result is not None
        assert result.total_area_sqm > 0
        assert 0.0 <= result.plantable_area_sqm <= result.total_area_sqm
        assert result.plantable_ratio == round(result.plantable_area_sqm / result.total_area_sqm, 4)


def test_building_count_matches_scene_meta(location_scene):
    for scene in location_scene.values():
        result = characterize_site(scene)
        assert result.building_count == scene.meta.buildingCount


def test_restriction_zone_counts_sum_to_all_restrictions(scene_02):
    result = characterize_site(scene_02)
    assert sum(result.restriction_zone_counts.values()) == len(scene_02.restrictions)


def test_no_existing_trees_on_synthetic_locations(location_scene):
    # Синтетические тестовые участки не содержат объектов type="tree" -- это
    # факт о фикстурах, а не про generate_trees (тот их и генерирует).
    for scene in location_scene.values():
        assert characterize_site(scene).existing_tree_species == {}


def test_territory_type_guess_is_playground_before_road(location_scene):
    # 03_lshape_vs_rect: есть playground_zone -- должен распознаться как двор,
    # даже при наличии дорожек (pedestrian_path, не road).
    result = characterize_site(location_scene[3])
    assert result.territory_type == "двор"
    assert result.territory_type_is_heuristic is True


def test_territory_type_guess_road_without_playground():
    # Ни один из шести тестовых участков не содержит "road" без единой
    # playground_zone одновременно -- собираем минимальную сцену, чтобы
    # проверить эту ветку правила напрямую.
    square = [Point2(x=0, z=0), Point2(x=50, z=0), Point2(x=50, z=50), Point2(x=0, z=50)]
    road = RestrictionZone(
        id="r1", type="road", name="Проезжая часть", polygon=square, severity="forbidden", minDistance=2.0, message=""
    )
    scene = Scene(boundary=Boundary(polygon=square, sourceLayer="BOUNDARY"), restrictions=[road], objects=[], meta=_meta())
    assert characterize_site(scene).territory_type == "улица"


def test_territory_type_guess_road_and_playground_together_is_dvor(location_scene):
    # 05_klykova_avenue -- единственный тестовый участок с type="road", но у
    # него же есть playground_zone: по документированному приоритету это
    # "двор", не "улица" (см. докстринг _guess_territory_type).
    result = characterize_site(location_scene[5])
    assert result.territory_type == "двор"


def test_territory_type_guess_undetermined_without_signals(scene_02):
    # 02_courtyard_3buildings: ни playground_zone, ни road.
    assert characterize_site(scene_02).territory_type == "неопределено"


def test_usable_planting_area_empty_without_boundary():
    scene = Scene(boundary=None, restrictions=[], objects=[], meta=_meta())
    assert usable_planting_area(scene).is_empty
